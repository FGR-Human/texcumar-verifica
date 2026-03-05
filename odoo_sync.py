#!/usr/bin/env python3
"""
odoo_sync.py — TEXCUMAR v3 DEFINITIVO
Sincroniza account.remission.guide → Google Sheets (pestaña 'Guias')

MAPEO CONFIRMADO por diagnóstico directo de Odoo 17:

  ENCABEZADO (account.remission.guide):
    numero            ← l10n_latam_document_number
    autorizacion      ← l10n_ec_authorization_number
    fechaAutorizacion ← l10n_ec_authorization_date
    base              ← warehouse_id[name]
    fechaInicio       ← date_start
    fechaFin          ← date_end
    placa             ← license_plate
    cantidad          ← animal_qty_total
    codigoSCI         ← NO EXISTE en Odoo → vacío
    globalGAP         ← NO EXISTE en Odoo → vacío

  CLIENTE (res.partner via client_id):
    destinatario ← parent_name (empresa madre) o name
    rucDestino   ← vat
    nombreDest   ← name (nombre del punto de entrega)
    destino      ← state_id + city + sector
    llegada      ← sector[name] o street

  TRANSPORTISTA (res.partner via partner_id):
    transportista ← name
    rucTransp     ← vat

  LÍNEA DE GUÍA (account.remission.guide.line via line_ids):
    motivo       ← reason_id[name]
    picking_id   → para obtener stock.move

  STOCK MOVE (stock.move via picking_id, state=done):
    codProducto  ← product_id[name] → extraer [CODE]
    unidad       ← product_uom[name]
    descripcion  ← description_picking
    cantBruta    ← gross_quantity

  GUIDE STOCK LINE (account.remission.guide.stock.line via stock_move_lines):
    tecnico, despacho, gavetas, plus ← auto-descubiertos
"""

import os, re, sys, json, xmlrpc.client
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

# ─── CONFIG ──────────────────────────────────────────────────────────────────

ODOO_URL      = os.environ["ODOO_URL"].rstrip("/")
ODOO_DB       = os.environ["ODOO_DB"]
ODOO_USER     = os.environ["ODOO_USER"]
ODOO_PASSWORD = os.environ["ODOO_PASSWORD"]
SHEET_ID      = os.environ["GOOGLE_SHEET_ID"]
CREDS_JSON    = os.environ["GOOGLE_CREDS_JSON"]

SHEET_TAB  = "Guias"
BATCH_SIZE = 200

COLUMNS = [
    "numero", "autorizacion", "fechaAutorizacion", "base",
    "codigoSCI", "globalGAP", "fechaInicio", "fechaFin",
    "destinatario", "rucDestino", "nombreDest", "destino",
    "llegada", "motivo", "transportista", "rucTransp", "placa",
    "codProducto", "unidad", "descripcion", "cantidad", "cantBruta",
    "tecnico", "despacho", "gavetas", "plus",
]

# Candidatos para campos de guide.stock.line (en orden de preferencia)
TECNICO_CANDIDATES  = ["technician_id", "technician", "tech_id", "x_tecnico",
                        "responsible_id", "user_id", "employee_id"]
DESPACHO_CANDIDATES = ["dispatch_type", "packaging_type", "x_despacho",
                        "container_type", "packing", "package_type_id"]
GAVETAS_CANDIDATES  = ["containers", "gavetas", "x_gavetas", "qty_packages",
                        "number_of_packages", "packages", "cardboard", "boxes"]
PLUS_CANDIDATES     = ["plus", "x_plus", "percentage_plus", "extra_qty",
                        "additional_qty", "bonus"]

# ─── ODOO RPC ─────────────────────────────────────────────────────────────────

def connect_odoo():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
    if not uid:
        sys.exit("❌ Autenticación Odoo fallida.")
    print(f"✅ Odoo conectado — UID {uid}")
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
    return uid, models

def rpc(models, uid, model, method, args, kw=None):
    return models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD, model, method, args, kw or {}
    )

def batch_search_read(models, uid, model, domain, fields, order="id asc"):
    """Descarga TODOS los registros en lotes."""
    total = rpc(models, uid, model, "search_count", [domain])
    print(f"   {model}: {total} registros")
    results, offset = [], 0
    while offset < total:
        chunk = rpc(models, uid, model, "search_read", [domain],
                    {"fields": fields, "limit": BATCH_SIZE,
                     "offset": offset, "order": order})
        if not chunk:
            break
        results.extend(chunk)
        offset += len(chunk)
        if total > BATCH_SIZE:
            print(f"   {model}: {offset}/{total} ({int(offset/total*100)}%)")
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
    """'[N50002] Nauplios...' → 'N50002'"""
    if not product_name:
        return ""
    m = re.match(r'\[([^\]]+)\]', str(product_name))
    return m.group(1) if m else ""

def strip_code(product_name):
    """'[N50002] Nauplios...' → 'Nauplios...'"""
    if not product_name:
        return ""
    return re.sub(r'^\[[^\]]+\]\s*', '', str(product_name)).strip()

def build_destino(partner):
    """Construye 'Provincia - CANTON - Sector' desde el partner."""
    state  = re.sub(r'\s*\(EC\)\s*', '', m2o_name(partner.get("state_id"))).strip()
    city   = safe(partner.get("city", ""))
    sector = m2o_name(partner.get("sector"))
    street = safe(partner.get("street", ""))
    punto  = sector or street
    parts  = [p for p in [state, city, punto] if p]
    return " - ".join(parts)

def first_val(record, candidates):
    """Primer campo no-vacío de la lista de candidatos."""
    if not record:
        return ""
    for c in candidates:
        v = record.get(c)
        if v is not False and v is not None and str(v).strip() not in ("", "False", "0"):
            if isinstance(v, (list, tuple)) and len(v) > 1:
                return str(v[1]).strip()
            return safe(v)
    return ""

# ─── FETCH ────────────────────────────────────────────────────────────────────

def fetch_guides(models, uid):
    fields = [
        "id", "name", "l10n_latam_document_number",
        "l10n_ec_authorization_number", "l10n_ec_authorization_date",
        "date_start", "date_end", "date",
        "warehouse_id", "client_id", "partner_id",
        "license_plate", "animal_qty_total",
        "state", "line_ids",
    ]
    guides = batch_search_read(models, uid,
                               "account.remission.guide",
                               [("state", "=", "posted")], fields)
    if not guides:
        sys.exit("❌ Sin guías en estado 'posted'.")
    return guides


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
    # Descubrir campos disponibles (para no fallar si gross_quantity no existe)
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
    """
    Descarga account.remission.guide.stock.line y auto-descubre sus campos.
    Estos son los campos donde viven tecnico, despacho, gavetas, plus.
    """
    if not stock_line_ids:
        return {}, {}
    try:
        fields_meta = rpc(models, uid,
                          "account.remission.guide.stock.line",
                          "fields_get", [],
                          {"attributes": ["string", "type", "relation"]})
    except Exception as e:
        print(f"   ⚠️  account.remission.guide.stock.line no accesible: {e}")
        return {}, {}

    print(f"\n   📋 account.remission.guide.stock.line — {len(fields_meta)} campos:")
    for fn in sorted(fields_meta.keys()):
        fm = fields_meta[fn]
        print(f"      {fn:<40} {fm['type']:<12} {fm.get('string','')}")

    # Solo campos simples (no binarios ni one2many pesados)
    exclude = {"binary", "html", "one2many", "many2many"}
    fields  = [f for f, m in fields_meta.items() if m["type"] not in exclude]

    try:
        records = batch_read(models, uid,
                             "account.remission.guide.stock.line",
                             list(stock_line_ids), fields)
        by_id = {r["id"]: r for r in records}

        # Mostrar un ejemplo para diagnóstico
        if records:
            print(f"\n   Ejemplo guide.stock.line id={records[0]['id']}:")
            for k, v in records[0].items():
                if v is not False and v is not None and v != "" and v != []:
                    print(f"      {k}: {repr(v)[:80]}")

        return by_id, fields_meta
    except Exception as e:
        print(f"   ⚠️  Error leyendo guide.stock.line: {e}")
        return {}, fields_meta


def fetch_partners(models, uid, partner_ids):
    if not partner_ids:
        return {}
    fields = [
        "id", "name", "vat",
        "parent_id", "parent_name",
        "street", "city", "state_id",
        "sector",   # punto de llegada personalizado TEXCUMAR
        "tex_city",
        "type", "function",
    ]
    records = batch_read(models, uid, "res.partner",
                         list(set(partner_ids)), fields)
    return {r["id"]: r for r in records}

# ─── BUILD ROW ────────────────────────────────────────────────────────────────

def build_row(guide, guide_line, stock_move, client_p, carrier_p,
              guide_stock_line, stock_line_fields):

    # ── ENCABEZADO ────────────────────────────────────────────────────────
    numero = safe(guide.get("l10n_latam_document_number") or guide.get("name", ""))
    # Eliminar prefijo 'REM ' si viene del campo name
    if numero.upper().startswith("REM "):
        numero = numero[4:].strip()

    autorizacion      = safe(guide.get("l10n_ec_authorization_number", ""))
    fechaAutorizacion = fmt_date(guide.get("l10n_ec_authorization_date")
                                 or guide.get("date"))
    base              = m2o_name(guide.get("warehouse_id"))
    fechaInicio       = fmt_date(guide.get("date_start") or guide.get("date"))
    fechaFin          = fmt_date(guide.get("date_end"))
    placa             = safe(guide.get("license_plate", ""))
    cantidad          = safe(guide.get("animal_qty_total", ""))
    codigoSCI         = ""   # campo no existe en Odoo
    globalGAP         = ""   # campo no existe en Odoo

    # ── CLIENTE ───────────────────────────────────────────────────────────
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
        rucDestino   = ""
        nombreDest   = ""
        destino      = ""
        llegada      = ""

    # ── MOTIVO ────────────────────────────────────────────────────────────
    motivo = m2o_name(guide_line.get("reason_id")) if guide_line else ""

    # ── TRANSPORTISTA ─────────────────────────────────────────────────────
    if carrier_p:
        transportista = safe(carrier_p.get("name", ""))
        rucTransp     = safe(carrier_p.get("vat", ""))
    else:
        transportista = m2o_name(guide.get("partner_id"))
        rucTransp     = ""

    # ── PRODUCTO (stock.move) ─────────────────────────────────────────────
    codProducto = ""
    unidad      = ""
    descripcion = ""
    cantBruta   = ""

    if stock_move:
        product_display = m2o_name(stock_move.get("product_id"))
        codProducto     = extract_code(product_display)
        unidad          = m2o_name(stock_move.get("product_uom"))
        descripcion     = safe(stock_move.get("description_picking", ""))
        if not descripcion:
            descripcion = strip_code(product_display) or safe(stock_move.get("name", ""))
        cantBruta = safe(stock_move.get("gross_quantity", ""))

    # ── GUIDE STOCK LINE ──────────────────────────────────────────────────
    # Si los campos de guide.stock.line están poblados los usa;
    # si no, intenta desde stock_move (campos custom de TEXCUMAR)
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

def get_worksheet():
    creds = Credentials.from_service_account_info(
        json.loads(CREDS_JSON),
        scopes=["https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(SHEET_TAB)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_TAB, rows=12000, cols=len(COLUMNS))
        print(f"✅ Pestaña '{SHEET_TAB}' creada")
    return ws

def write_sheet(ws, rows):
    ws.clear()
    all_data = [COLUMNS] + rows
    CHUNK = 3000
    for i in range(0, len(all_data), CHUNK):
        ws.update(all_data[i:i+CHUNK], f"A{i+1}", value_input_option="RAW")
    print(f"✅ {len(rows)} filas escritas en '{SHEET_TAB}'")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("TEXCUMAR — Sync Odoo → Google Sheets v3")
    print(f"Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    uid, models = connect_odoo()

    # 1. Guías
    print("\n📥 1/6  Guías (state=posted)...")
    guides = fetch_guides(models, uid)
    guide_ids   = [g["id"] for g in guides]
    client_ids  = [m2o_id(g.get("client_id"))  for g in guides]
    carrier_ids = [m2o_id(g.get("partner_id")) for g in guides]

    # 2. Líneas de guía
    print("\n📥 2/6  Líneas de guía...")
    lines_by_guide = fetch_guide_lines(models, uid, guide_ids)
    picking_ids      = set()
    stock_line_ids   = set()
    for lines in lines_by_guide.values():
        for ln in lines:
            if ln.get("picking_id"):
                picking_ids.add(m2o_id(ln["picking_id"]))
            for slid in (ln.get("stock_move_lines") or []):
                stock_line_ids.add(slid)
    print(f"   {sum(len(v) for v in lines_by_guide.values())} líneas | "
          f"{len(picking_ids)} pickings | {len(stock_line_ids)} stock.lines")

    # 3. Stock moves
    print("\n📥 3/6  stock.move (producto, unidad, cantBruta)...")
    moves_by_picking = fetch_stock_moves(models, uid, picking_ids)
    print(f"   {len(moves_by_picking)} movimientos")

    # 4. Guide stock lines (tecnico, despacho, gavetas, plus)
    print("\n📥 4/6  account.remission.guide.stock.line...")
    stock_lines_by_id, stock_line_fields = fetch_guide_stock_lines(
        models, uid, stock_line_ids)
    print(f"   {len(stock_lines_by_id)} registros")

    # 5. Partners
    print("\n📥 5/6  Partners (clientes + transportistas)...")
    all_pids = list(set(filter(None, client_ids + carrier_ids)))
    partners = fetch_partners(models, uid, all_pids)
    print(f"   {len(partners)} partners")

    # 6. Construir filas
    print("\n🔨 6/6  Construyendo filas...")
    rows = []
    stat = {"no_move": 0, "no_client": 0, "no_stockline": 0}

    for guide in guides:
        gid         = guide["id"]
        guide_lines = lines_by_guide.get(gid, [])
        guide_line  = guide_lines[0] if guide_lines else None

        # stock.move via picking
        stock_move = None
        if guide_line and guide_line.get("picking_id"):
            stock_move = moves_by_picking.get(m2o_id(guide_line["picking_id"]))
        if not stock_move:
            stat["no_move"] += 1

        # guide.stock.line
        gsl = None
        if guide_line and guide_line.get("stock_move_lines"):
            gsl = stock_lines_by_id.get(guide_line["stock_move_lines"][0])
        if not gsl:
            stat["no_stockline"] += 1

        # partners
        client_p  = partners.get(m2o_id(guide.get("client_id")))
        carrier_p = partners.get(m2o_id(guide.get("partner_id")))
        if not client_p:
            stat["no_client"] += 1

        rows.append(build_row(guide, guide_line, stock_move,
                               client_p, carrier_p, gsl, stock_line_fields))

    # Verificar candidatos en stock.line
    if stock_line_fields:
        sl_keys = set(stock_line_fields.keys())
        print(f"\n   📋 Verificación campos en guide.stock.line:")
        for campo, cands in [("tecnico",  TECNICO_CANDIDATES),
                              ("despacho", DESPACHO_CANDIDATES),
                              ("gavetas",  GAVETAS_CANDIDATES),
                              ("plus",     PLUS_CANDIDATES)]:
            found = [c for c in cands if c in sl_keys]
            status = f"✅ {found}" if found else "❌ NINGUNO — campo pendiente de mapear"
            print(f"      {campo:<10}: {status}")

    # Escribir
    print(f"\n📤 Escribiendo {len(rows)} filas al Sheet...")
    ws = get_worksheet()
    write_sheet(ws, rows)

    # Resumen de cobertura
    print("\n" + "=" * 65)
    print(f"✅ SYNC COMPLETADO — {len(rows)} registros")
    print(f"   Sin stock.move:  {stat['no_move']}")
    print(f"   Sin client_id:   {stat['no_client']}")
    print(f"   Sin stock.line:  {stat['no_stockline']}")
    print(f"\n   Cobertura por campo:")
    for col in ["numero", "autorizacion", "base", "destinatario",
                "rucDestino", "nombreDest", "destino", "llegada",
                "motivo", "transportista", "rucTransp", "placa",
                "codProducto", "unidad", "descripcion",
                "cantidad", "cantBruta",
                "tecnico", "despacho", "gavetas", "plus"]:
        idx = COLUMNS.index(col)
        n   = sum(1 for r in rows if r[idx] and str(r[idx]).strip())
        pct = int(n / len(rows) * 100) if rows else 0
        ico = "✅" if pct >= 80 else ("⚠️ " if pct >= 10 else "❌")
        print(f"   {ico} {col:<18}: {n:>6}/{len(rows)} ({pct}%)")

    print(f"\nFin: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)


if __name__ == "__main__":
    main()
