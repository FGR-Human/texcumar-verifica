#!/usr/bin/env python3
"""
odoo_sync.py — TEXCUMAR v4 PRODUCCIÓN
Sincroniza account.remission.guide (Odoo) → Google Sheets (pestaña 'Guias')

NOVEDADES v4 vs v3:
  ✅ URL de PRODUCCIÓN: https://texcumar.odoo.com
  ✅ DELTA SYNC: solo descarga guías nuevas/modificadas desde la última ejecución
     → Sync completo (primer run o --full): 5-10 min para ~7,648 guías
     → Sync incremental (runs posteriores): 5-30 seg (solo guías del día)
  ✅ SMART WRITE: actualiza filas existentes en lugar de borrar+reescribir todo
  ✅ BATCH_SIZE aumentado a 300 para reducir llamadas XML-RPC
  ✅ Metadata tab: guarda timestamp de último sync en pestaña '_Sync_Meta'
  ✅ Flag --full para forzar sincronización completa
  ✅ Flag --desde YYYY-MM-DD para sincronizar desde una fecha específica
"""

import os, re, sys, json, xmlrpc.client, argparse
from datetime import datetime, timedelta, timezone

import gspread
from google.oauth2.service_account import Credentials

# ─── CONFIGURACIÓN ────────────────────────────────────────────────────────────
# Todas las variables sensibles vienen de environment variables (GitHub Secrets)
# NUNCA hardcodear credenciales en el código.

ODOO_URL      = os.environ.get("ODOO_URL", "https://texcumar.odoo.com").rstrip("/")
ODOO_DB       = os.environ["ODOO_DB"]
ODOO_USER     = os.environ["ODOO_USER"]
ODOO_PASSWORD = os.environ["ODOO_PASSWORD"]

SHEET_ID      = os.environ["GOOGLE_SHEET_ID"]
CREDS_JSON    = os.environ["GOOGLE_CREDS_JSON"]

SHEET_TAB     = "Guias"
META_TAB      = "_Sync_Meta"   # pestaña para guardar el estado del sync
BATCH_SIZE    = 300            # registros por llamada XML-RPC (aumentado de 200)
DELTA_BUFFER  = 2              # horas de buffer para evitar perder guías en edge cases

# ─── COLUMNAS (orden = columnas del Sheet) ────────────────────────────────────
COLUMNS = [
    "numero", "autorizacion", "fechaAutorizacion", "base",
    "codigoSCI", "globalGAP", "fechaInicio", "fechaFin",
    "destinatario", "rucDestino", "nombreDest", "destino",
    "llegada", "motivo", "transportista", "rucTransp", "placa",
    "codProducto", "unidad", "descripcion", "cantidad", "cantBruta",
    "tecnico", "despacho", "gavetas", "plus",
]

# Candidatos para campos custom de TEXCUMAR
TECNICO_CANDIDATES  = ["technician_id", "technician", "tech_id", "x_tecnico",
                        "responsible_id", "user_id", "employee_id"]
DESPACHO_CANDIDATES = ["dispatch_type", "packaging_type", "x_despacho",
                        "container_type", "packing", "package_type_id"]
GAVETAS_CANDIDATES  = ["containers", "gavetas", "x_gavetas", "qty_packages",
                        "number_of_packages", "packages", "cardboard", "boxes"]
PLUS_CANDIDATES     = ["plus", "x_plus", "percentage_plus", "extra_qty",
                        "additional_qty", "bonus"]

# ─── CONEXIÓN ODOO ────────────────────────────────────────────────────────────
def connect_odoo():
    print(f"🔌 Conectando a Odoo: {ODOO_URL}")
    common = xmlrpc.client.ServerProxy(
        f"{ODOO_URL}/xmlrpc/2/common", allow_none=True
    )
    uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
    if not uid:
        sys.exit("❌ Autenticación Odoo fallida. Verificar credenciales y DB.")
    print(f"✅ Odoo conectado — Usuario: {ODOO_USER} | UID: {uid} | DB: {ODOO_DB}")
    models = xmlrpc.client.ServerProxy(
        f"{ODOO_URL}/xmlrpc/2/object", allow_none=True
    )
    return uid, models


def rpc(models, uid, model, method, args, kw=None):
    return models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD, model, method, args, kw or {}
    )


def batch_search_read(models, uid, model, domain, fields, order="id asc"):
    """Descarga todos los registros en lotes de BATCH_SIZE."""
    total = rpc(models, uid, model, "search_count", [domain])
    print(f"   {model}: {total} registros en dominio")
    results, offset = [], 0
    while offset < total:
        chunk = rpc(models, uid, model, "search_read", [domain], {
            "fields": fields, "limit": BATCH_SIZE,
            "offset": offset, "order": order
        })
        if not chunk:
            break
        results.extend(chunk)
        offset += len(chunk)
        if total > BATCH_SIZE:
            pct = int(offset / total * 100)
            print(f"   ↓ {offset}/{total} ({pct}%)", end="\r")
    if total > BATCH_SIZE:
        print()
    return results


def batch_read(models, uid, model, ids, fields):
    """Lee registros por lista de IDs."""
    if not ids:
        return []
    ids = list(set(ids))
    results = []
    for i in range(0, len(ids), BATCH_SIZE):
        chunk = rpc(models, uid, model, "read",
                    [ids[i:i+BATCH_SIZE]], {"fields": fields})
        results.extend(chunk)
    return results

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def fmt_date(val):
    if not val or val is False:
        return ""
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return str(val)


def m2o_name(val):
    if not val or val is False:
        return ""
    return str(val[1]).strip() if isinstance(val, (list, tuple)) and len(val) > 1 else str(val).strip()


def m2o_id(val):
    if not val or val is False:
        return None
    return val[0] if isinstance(val, (list, tuple)) else int(val)


def safe(val):
    if val is False or val is None:
        return ""
    if isinstance(val, float):
        return str(int(val)) if val == int(val) else str(round(val, 4))
    return str(val).strip()


def extract_code(product_name):
    if not product_name:
        return ""
    m = re.match(r'\[([^\]]+)\]', str(product_name))
    return m.group(1) if m else ""


def strip_code(product_name):
    if not product_name:
        return ""
    return re.sub(r'^\[[^\]]+\]\s*', '', str(product_name)).strip()


def build_destino(partner):
    state  = re.sub(r'\s*\(EC\)\s*', '', m2o_name(partner.get("state_id"))).strip()
    city   = safe(partner.get("city", ""))
    sector = m2o_name(partner.get("sector"))
    street = safe(partner.get("street", ""))
    punto  = sector or street
    parts  = [p for p in [state, city, punto] if p]
    return " - ".join(parts)


def first_val(record, candidates):
    if not record:
        return ""
    for c in candidates:
        v = record.get(c)
        if v is not False and v is not None and str(v).strip() not in ("", "False", "0"):
            if isinstance(v, (list, tuple)) and len(v) > 1:
                return str(v[1]).strip()
            return safe(v)
    return ""

# ─── FETCH ODOO ───────────────────────────────────────────────────────────────
def fetch_guides(models, uid, since_dt=None):
    """
    Fetches guides desde Odoo.
    Si since_dt es un datetime, solo trae guías modificadas después de ese momento
    (delta sync). Si es None, trae todas (full sync).
    """
    domain = [("state", "=", "posted")]
    if since_dt:
        # write_date cubre tanto creación como modificación
        since_str = since_dt.strftime("%Y-%m-%d %H:%M:%S")
        domain.append(("write_date", ">=", since_str))
        print(f"   🔄 Delta sync desde: {since_str}")
    else:
        print("   🔁 Full sync (todas las guías posted)")

    fields = [
        "id", "name", "l10n_latam_document_number",
        "l10n_ec_authorization_number", "l10n_ec_authorization_date",
        "date_start", "date_end", "date",
        "warehouse_id", "client_id", "partner_id",
        "license_plate", "animal_qty_total",
        "state", "line_ids", "write_date",
    ]
    return batch_search_read(models, uid, "account.remission.guide", domain, fields,
                             order="write_date desc")  # más recientes primero


def fetch_guide_lines(models, uid, guide_ids):
    fields = ["id", "guide_id", "picking_id", "stock_move_lines",
              "reason_id", "animal_qty", "partner_id"]
    lines = batch_search_read(models, uid,
                              "account.remission.guide.line",
                              [("guide_id", "in", guide_ids)], fields)
    by_guide = {}
    for ln in lines:
        gid = m2o_id(ln["guide_id"])
        by_guide.setdefault(gid, []).append(ln)
    return by_guide


def fetch_stock_moves(models, uid, picking_ids):
    if not picking_ids:
        return {}
    try:
        available = set(rpc(models, uid, "stock.move", "fields_get", [],
                            {"attributes": ["type"]}).keys())
    except Exception:
        available = set()

    want = ["id", "picking_id", "product_id", "product_uom",
            "description_picking", "product_uom_qty", "quantity",
            "gross_quantity", "name", "cardboard", "additional"]
    fields = [f for f in want if not available or f in available]

    moves = batch_search_read(models, uid, "stock.move",
                              [("picking_id", "in", list(picking_ids)),
                               ("state", "=", "done")], fields)
    by_picking = {}
    for mv in moves:
        pid = m2o_id(mv.get("picking_id"))
        if pid and pid not in by_picking:
            by_picking[pid] = mv
    return by_picking


def fetch_guide_stock_lines(models, uid, stock_line_ids):
    if not stock_line_ids:
        return {}, {}
    try:
        fields_meta = rpc(models, uid,
                          "account.remission.guide.stock.line",
                          "fields_get", [],
                          {"attributes": ["string", "type", "relation"]})
    except Exception as e:
        print(f"   ⚠️  guide.stock.line no accesible: {e}")
        return {}, {}

    exclude = {"binary", "html", "one2many", "many2many"}
    fields  = [f for f, m in fields_meta.items() if m["type"] not in exclude]
    try:
        records = batch_read(models, uid,
                             "account.remission.guide.stock.line",
                             list(stock_line_ids), fields)
        return {r["id"]: r for r in records}, fields_meta
    except Exception as e:
        print(f"   ⚠️  Error leyendo guide.stock.line: {e}")
        return {}, fields_meta


def fetch_partners(models, uid, partner_ids):
    if not partner_ids:
        return {}
    fields = [
        "id", "name", "vat", "parent_id", "parent_name",
        "street", "city", "state_id", "sector", "tex_city", "type",
    ]
    records = batch_read(models, uid, "res.partner",
                         list(set(partner_ids)), fields)
    return {r["id"]: r for r in records}

# ─── BUILD ROW ────────────────────────────────────────────────────────────────
def build_row(guide, guide_line, stock_move, client_p, carrier_p,
              guide_stock_line, stock_line_fields):
    # ENCABEZADO
    numero = safe(guide.get("l10n_latam_document_number") or guide.get("name", ""))
    if numero.upper().startswith("REM "):
        numero = numero[4:].strip()

    autorizacion      = safe(guide.get("l10n_ec_authorization_number", ""))
    fechaAutorizacion = fmt_date(guide.get("l10n_ec_authorization_date") or guide.get("date"))
    base              = m2o_name(guide.get("warehouse_id"))
    fechaInicio       = fmt_date(guide.get("date_start") or guide.get("date"))
    fechaFin          = fmt_date(guide.get("date_end"))
    placa             = safe(guide.get("license_plate", ""))
    cantidad          = safe(guide.get("animal_qty_total", ""))
    codigoSCI         = ""
    globalGAP         = ""

    # CLIENTE
    if client_p:
        parent_name  = safe(client_p.get("parent_name", ""))
        own_name     = safe(client_p.get("name", ""))
        destinatario = parent_name if parent_name else own_name
        nombreDest   = own_name
        rucDestino   = safe(client_p.get("vat", ""))
        destino      = build_destino(client_p)
        llegada      = m2o_name(client_p.get("sector")) or safe(client_p.get("street", ""))
    else:
        destinatario = m2o_name(guide.get("client_id"))
        rucDestino = nombreDest = destino = llegada = ""

    # MOTIVO
    motivo = m2o_name(guide_line.get("reason_id")) if guide_line else ""

    # TRANSPORTISTA
    if carrier_p:
        transportista = safe(carrier_p.get("name", ""))
        rucTransp     = safe(carrier_p.get("vat", ""))
    else:
        transportista = m2o_name(guide.get("partner_id"))
        rucTransp     = ""

    # PRODUCTO (stock.move)
    codProducto = unidad = descripcion = cantBruta = ""
    if stock_move:
        product_display = m2o_name(stock_move.get("product_id"))
        codProducto     = extract_code(product_display)
        unidad          = m2o_name(stock_move.get("product_uom"))
        descripcion     = safe(stock_move.get("description_picking", ""))
        if not descripcion:
            descripcion = strip_code(product_display) or safe(stock_move.get("name", ""))
        cantBruta = safe(stock_move.get("gross_quantity", ""))

    # GUIDE STOCK LINE (campos custom)
    def get_campo(candidates):
        v = first_val(guide_stock_line, candidates)
        if not v and stock_move:
            v = first_val(stock_move, candidates)
        return v

    tecnico  = get_campo(TECNICO_CANDIDATES)
    despacho = get_campo(DESPACHO_CANDIDATES)
    gavetas  = get_campo(GAVETAS_CANDIDATES)
    plus     = get_campo(PLUS_CANDIDATES)

    return [
        numero, autorizacion, fechaAutorizacion, base,
        codigoSCI, globalGAP, fechaInicio, fechaFin,
        destinatario, rucDestino, nombreDest, destino,
        llegada, motivo, transportista, rucTransp, placa,
        codProducto, unidad, descripcion, cantidad, cantBruta,
        tecnico, despacho, gavetas, plus,
    ]

# ─── GOOGLE SHEETS ────────────────────────────────────────────────────────────
def get_gspread_client():
    creds = Credentials.from_service_account_info(
        json.loads(CREDS_JSON),
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
    )
    return gspread.authorize(creds)


def get_or_create_tab(sh, tab_name, rows=1, cols=5):
    try:
        return sh.worksheet(tab_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=rows, cols=cols)
        print(f"   ✅ Pestaña '{tab_name}' creada")
        return ws


def read_last_sync(sh):
    """Lee el timestamp del último sync exitoso desde la pestaña de metadata."""
    try:
        ws   = sh.worksheet(META_TAB)
        data = ws.get_all_values()
        for row in data:
            if row and row[0] == "last_sync_at" and len(row) > 1 and row[1]:
                return datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return None


def write_last_sync(sh, dt=None):
    """Guarda el timestamp del sync actual en la pestaña de metadata."""
    if dt is None:
        dt = datetime.utcnow()
    ts  = dt.strftime("%Y-%m-%d %H:%M:%S")
    ws  = get_or_create_tab(sh, META_TAB, rows=10, cols=3)
    ws.clear()
    ws.update([
        ["Clave",        "Valor",                           "Descripción"],
        ["last_sync_at", ts,                                "Última sincronización exitosa (UTC)"],
        ["odoo_url",     ODOO_URL,                          "URL de Odoo utilizada"],
        ["odoo_db",      ODOO_DB,                           "Base de datos Odoo"],
        ["sheet_tab",    SHEET_TAB,                         "Pestaña de datos"],
        ["version",      "v4",                              "Versión del script"],
    ], "A1")
    print(f"   💾 Metadata guardada: last_sync_at = {ts} (UTC)")


def get_existing_index(ws):
    """
    Lee la columna A del Sheet y construye un dict {numero → numero_de_fila_1indexed}.
    Esto permite actualizar filas existentes sin reescribir todo.
    """
    try:
        col_a = ws.col_values(1)  # solo columna A, mucho más rápido que getDataRange
    except Exception:
        col_a = []
    index = {}
    for i, val in enumerate(col_a):
        if i == 0:
            continue  # saltar header
        if val and str(val).strip():
            index[str(val).strip()] = i + 1  # 1-indexed
    return index


def smart_write(ws, new_rows, is_full_sync):
    """
    FULL SYNC: borra y reescribe todo (igual que v3).
    DELTA SYNC: 
      - Filas cuyo 'numero' YA existe en el Sheet → actualiza esa fila
      - Filas nuevas → append al final
    """
    if is_full_sync or not new_rows:
        # Full: borrar todo y reescribir
        ws.clear()
        all_data = [COLUMNS] + new_rows
        CHUNK = 3000
        for i in range(0, len(all_data), CHUNK):
            ws.update(all_data[i:i + CHUNK], f"A{i + 1}",
                      value_input_option="RAW")
        print(f"   ✅ Full write: {len(new_rows)} filas")
        return

    # Delta: leer índice existente
    print("   📖 Leyendo índice de filas existentes...")
    existing = get_existing_index(ws)
    print(f"   📋 {len(existing)} guías ya en el Sheet")

    updates  = []   # (sheet_row_1indexed, row_data)
    appends  = []   # row_data nuevas

    for row in new_rows:
        numero = str(row[0]).strip()
        if numero in existing:
            updates.append((existing[numero], row))
        else:
            appends.append(row)

    print(f"   🔄 Actualizando: {len(updates)} filas | Nuevas: {len(appends)} filas")

    # Actualizar filas existentes en batch (grouped para minimizar llamadas API)
    if updates:
        # gspread batch_update acepta lista de {range, values}
        batch = []
        for (sheet_row, row_data) in updates:
            range_str = f"A{sheet_row}"
            batch.append({"range": range_str, "values": [row_data]})
        # Enviar en grupos de 500
        for i in range(0, len(batch), 500):
            ws.batch_update(batch[i:i + 500], value_input_option="RAW")
        print(f"   ✅ {len(updates)} filas actualizadas")

    # Append nuevas filas
    if appends:
        ws.append_rows(appends, value_input_option="RAW",
                       insert_data_option="INSERT_ROWS")
        print(f"   ✅ {len(appends)} filas nuevas insertadas")

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="TEXCUMAR Odoo → Sheets Sync v4")
    parser.add_argument("--full",   action="store_true",
                        help="Forzar sincronización completa (ignorar delta)")
    parser.add_argument("--desde",  type=str, default=None,
                        help="Sincronizar desde fecha YYYY-MM-DD (ej: --desde 2025-01-01)")
    args = parser.parse_args()

    print("=" * 65)
    print("TEXCUMAR — Odoo → Google Sheets Sync v4 (PRODUCCIÓN)")
    print(f"Inicio : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} local")
    print(f"URL    : {ODOO_URL}")
    print(f"DB     : {ODOO_DB}")
    print("=" * 65)

    # ── Conectar ──────────────────────────────────────────────────────────────
    uid, models = connect_odoo()

    gc = get_gspread_client()
    sh = gc.open_by_key(SHEET_ID)
    ws = get_or_create_tab(sh, SHEET_TAB, rows=15000, cols=len(COLUMNS))

    # ── Determinar modo: full o delta ─────────────────────────────────────────
    is_full_sync = args.full
    since_dt     = None

    if args.desde:
        since_dt     = datetime.strptime(args.desde, "%Y-%m-%d")
        is_full_sync = False
        print(f"\n📅 Modo: DELTA desde {args.desde}")
    elif not is_full_sync:
        last_sync = read_last_sync(sh)
        if last_sync:
            # Restar buffer de seguridad
            since_dt = last_sync - timedelta(hours=DELTA_BUFFER)
            print(f"\n⏱  Modo: DELTA (último sync: {last_sync} UTC, buffer: -{DELTA_BUFFER}h)")
        else:
            is_full_sync = True
            print("\n🔁 Modo: FULL (no hay metadata de sync previo)")
    else:
        print("\n🔁 Modo: FULL (forzado con --full)")

    # ── Fetch guías ───────────────────────────────────────────────────────────
    print("\n📥 1/6 Guías...")
    guides    = fetch_guides(models, uid, since_dt if not is_full_sync else None)
    if not guides:
        print("✅ Sin guías nuevas/modificadas desde el último sync. Nada que hacer.")
        write_last_sync(sh)
        return

    guide_ids  = [g["id"] for g in guides]
    client_ids = [m2o_id(g.get("client_id"))  for g in guides]
    carrier_ids= [m2o_id(g.get("partner_id")) for g in guides]

    # ── Fetch datos relacionados ──────────────────────────────────────────────
    print(f"\n📥 2/6 Líneas de guía para {len(guide_ids)} guías...")
    lines_by_guide = fetch_guide_lines(models, uid, guide_ids)

    picking_ids    = set()
    stock_line_ids = set()
    for lines in lines_by_guide.values():
        for ln in lines:
            if ln.get("picking_id"):
                picking_ids.add(m2o_id(ln["picking_id"]))
            for slid in (ln.get("stock_move_lines") or []):
                stock_line_ids.add(slid)

    print(f"   {sum(len(v) for v in lines_by_guide.values())} líneas | "
          f"{len(picking_ids)} pickings | {len(stock_line_ids)} stock.lines")

    print("\n📥 3/6 Stock moves...")
    moves_by_picking = fetch_stock_moves(models, uid, picking_ids)
    print(f"   {len(moves_by_picking)} movimientos")

    print("\n📥 4/6 Guide stock lines (tecnico, despacho, gavetas, plus)...")
    stock_lines_by_id, stock_line_fields = fetch_guide_stock_lines(
        models, uid, stock_line_ids)
    print(f"   {len(stock_lines_by_id)} registros")

    print(f"\n📥 5/6 Partners ({len(set(filter(None, client_ids + carrier_ids)))} únicos)...")
    all_pids = list(set(filter(None, client_ids + carrier_ids)))
    partners = fetch_partners(models, uid, all_pids)
    print(f"   {len(partners)} partners cargados")

    # ── Construir filas ───────────────────────────────────────────────────────
    print(f"\n🔨 6/6 Construyendo {len(guides)} filas...")
    rows = []
    stat = {"no_move": 0, "no_client": 0, "no_stockline": 0}

    for guide in guides:
        gid         = guide["id"]
        guide_lines = lines_by_guide.get(gid, [])
        guide_line  = guide_lines[0] if guide_lines else None

        stock_move = None
        if guide_line and guide_line.get("picking_id"):
            stock_move = moves_by_picking.get(m2o_id(guide_line["picking_id"]))
        if not stock_move:
            stat["no_move"] += 1

        gsl = None
        if guide_line and guide_line.get("stock_move_lines"):
            gsl = stock_lines_by_id.get(guide_line["stock_move_lines"][0])
        if not gsl:
            stat["no_stockline"] += 1

        client_p  = partners.get(m2o_id(guide.get("client_id")))
        carrier_p = partners.get(m2o_id(guide.get("partner_id")))
        if not client_p:
            stat["no_client"] += 1

        rows.append(build_row(guide, guide_line, stock_move,
                              client_p, carrier_p, gsl, stock_line_fields))

    # ── Escribir en Google Sheets ─────────────────────────────────────────────
    print(f"\n📤 Escribiendo en Sheet (modo {'FULL' if is_full_sync else 'DELTA'})...")
    smart_write(ws, rows, is_full_sync)

    # ── Guardar metadata ──────────────────────────────────────────────────────
    write_last_sync(sh, datetime.utcnow())

    # ── Reporte final ─────────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print(f"✅ SYNC COMPLETADO — {len(rows)} guías procesadas")
    print(f"   Sin stock.move : {stat['no_move']}")
    print(f"   Sin client_id  : {stat['no_client']}")
    print(f"   Sin stock.line : {stat['no_stockline']}")
    print(f"\n   Cobertura por campo:")
    for col in ["numero", "autorizacion", "base", "destinatario",
                "rucDestino", "nombreDest", "destino", "llegada",
                "motivo", "transportista", "rucTransp", "placa",
                "codProducto", "unidad", "descripcion",
                "cantidad", "cantBruta"]:
        idx = COLUMNS.index(col)
        n   = sum(1 for r in rows if r[idx] and str(r[idx]).strip())
        pct = int(n / len(rows) * 100) if rows else 0
        ico = "✅" if pct >= 80 else ("⚠️ " if pct >= 10 else "❌")
        print(f"   {ico} {col:<18}: {n:>6}/{len(rows)} ({pct}%)")

    print(f"\nFin: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)


if __name__ == "__main__":
    main()
