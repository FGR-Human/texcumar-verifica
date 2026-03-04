import os
import json
import logging
import urllib.request
import time
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("texcumar-sync")

ODOO_URL      = os.environ["ODOO_URL"].rstrip("/")
ODOO_DB       = os.environ["ODOO_DB"]
ODOO_USER     = os.environ["ODOO_USER"]
ODOO_PASSWORD = os.environ["ODOO_PASSWORD"]
SHEET_ID      = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_CREDS  = os.environ["GOOGLE_CREDS_JSON"]
SHEET_TAB     = "Guias"

BATCH_SIZE    = 500   # guias por lote RPC
SHEETS_BATCH  = 200   # filas por lote escritura Sheets

COLUMNS = [
    "numero","autorizacion","fechaAutorizacion","base","codigoSCI","globalGAP",
    "destinatario","rucDestino","nombreDest","destino","llegada","motivo",
    "transportista","rucTransp","placa","partida","codProducto","unidad",
    "descripcion","cantidad","cantBruta","tecnico","despacho","gavetas",
    "plus","salinidad","temperatura",
]

SESSION_COOKIE = None


# ── RPC ───────────────────────────────────────────────────────────────────────
def rpc(endpoint, params, retries=3):
    global SESSION_COOKIE
    url = f"{ODOO_URL}{endpoint}"
    data = json.dumps({"jsonrpc":"2.0","method":"call","id":1,"params":params}).encode()
    headers = {"Content-Type": "application/json"}
    if SESSION_COOKIE:
        headers["Cookie"] = SESSION_COOKIE
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                if not SESSION_COOKIE:
                    raw = r.headers.get("Set-Cookie","")
                    if raw:
                        SESSION_COOKIE = raw.split(";")[0]
                resp = json.loads(r.read())
            if "error" in resp:
                raise RuntimeError(json.dumps(resp["error"])[:200])
            return resp["result"]
        except Exception as e:
            if attempt < retries - 1:
                log.warning(f"RPC intento {attempt+1} fallo: {str(e)[:100]}")
                time.sleep(2)
            else:
                raise


def authenticate():
    result = rpc("/web/session/authenticate", {
        "db": ODOO_DB, "login": ODOO_USER, "password": ODOO_PASSWORD
    })
    uid = result.get("uid")
    if not uid:
        raise RuntimeError("Autenticacion fallida")
    log.info(f"Odoo: autenticado UID={uid}")
    return uid


def search_read(model, domain, fields, limit=0, offset=0):
    return rpc("/web/dataset/call_kw", {
        "model": model, "method": "search_read",
        "args": [domain],
        "kwargs": {"fields": fields, "limit": limit,
                   "offset": offset, "order": "id asc"}
    })


# ── Helpers ───────────────────────────────────────────────────────────────────
def get_name(v):
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return str(v[1])
    return str(v) if isinstance(v, str) else ""


def fmt_date(v):
    if not v:
        return ""
    s = str(v)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return s


# ── Carga masiva de datos ─────────────────────────────────────────────────────
def get_all_guides():
    """Carga todas las guias en lotes de BATCH_SIZE."""
    log.info("Odoo: contando guias publicadas...")
    count_result = rpc("/web/dataset/call_kw", {
        "model": "account.remission.guide",
        "method": "search_count",
        "args": [[["state","=","posted"]]],
        "kwargs": {}
    })
    total = count_result
    log.info(f"Odoo: {total} guias a sincronizar")

    fields = [
        "l10n_latam_document_number","name","date_start","date_end_finalization",
        "license_plate","partner_id","client_id","warehouse_id","observation",
        "line_ids","l10n_ec_authorization_number","l10n_ec_authorization_date",
        "create_uid",
    ]

    all_guides = []
    offset = 0
    while offset < total:
        batch = search_read(
            "account.remission.guide",
            [["state","=","posted"]],
            fields, limit=BATCH_SIZE, offset=offset
        )
        all_guides.extend(batch)
        log.info(f"  Guias cargadas: {len(all_guides)}/{total}")
        offset += BATCH_SIZE
        time.sleep(0.3)

    return all_guides


def get_all_lines(all_line_ids):
    """Carga todas las lineas de producto en un solo lote."""
    if not all_line_ids:
        return {}
    log.info(f"Odoo: cargando {len(all_line_ids)} lineas de producto...")

    # Intentar cargar lineas — si falla usar campos minimos
    try:
        lines = search_read(
            "account.remission.guide.line",
            [["id","in", all_line_ids]],
            ["id","product_id","product_uom_id","product_qty","qty_done"]
        )
    except Exception as e:
        log.warning(f"Error cargando lineas completas: {e}")
        try:
            lines = search_read(
                "account.remission.guide.line",
                [["id","in", all_line_ids]],
                ["id","product_id","product_qty"]
            )
        except Exception as e2:
            log.warning(f"Error cargando lineas basicas: {e2}")
            return {}

    # Indexar por id para busqueda rapida
    return {l["id"]: l for l in lines}


def get_partner_vats(partner_ids):
    """Carga RUC de todos los partners en un solo query."""
    if not partner_ids:
        return {}
    unique_ids = list(set(partner_ids))
    log.info(f"Odoo: cargando RUC de {len(unique_ids)} partners...")
    try:
        partners = search_read(
            "res.partner",
            [["id","in", unique_ids]],
            ["id","vat"]
        )
        return {p["id"]: (p.get("vat") or "") for p in partners}
    except Exception as e:
        log.warning(f"Error cargando RUC: {e}")
        return {}


# ── Transformar ───────────────────────────────────────────────────────────────
def transform(guide, lines_map, vats_map):
    numero = (guide.get("l10n_latam_document_number") or
              get_name(guide.get("name","")).replace("REM ","").strip())

    # Producto de la primera linea
    descripcion = cod_producto = unidad = cantidad = cant_bruta = ""
    line_ids = guide.get("line_ids", [])
    if line_ids:
        first_line = lines_map.get(line_ids[0])
        if first_line:
            prod = first_line.get("product_id", False)
            descripcion = get_name(prod)
            cod_producto = str(prod[0]) if isinstance(prod,(list,tuple)) else ""
            uom = first_line.get("product_uom_id", False)
            unidad = get_name(uom)
            qty = first_line.get("qty_done") or first_line.get("product_qty") or 0
            cantidad = str(int(float(qty))) if qty else ""
            cant_bruta = cantidad

    # RUC de cliente y transportista
    client_raw = guide.get("client_id")
    transp_raw = guide.get("partner_id")
    client_id  = client_raw[0] if isinstance(client_raw,(list,tuple)) else None
    transp_id  = transp_raw[0] if isinstance(transp_raw,(list,tuple)) else None
    ruc_cliente = vats_map.get(client_id, "") if client_id else ""
    ruc_transp  = vats_map.get(transp_id, "") if transp_id else ""

    return [
        numero,
        str(guide.get("l10n_ec_authorization_number") or ""),
        fmt_date(guide.get("l10n_ec_authorization_date")),
        get_name(guide.get("warehouse_id")),
        "", "",
        get_name(client_raw),
        ruc_cliente,
        get_name(client_raw),
        "",
        fmt_date(guide.get("date_end_finalization")),
        str(guide.get("observation") or ""),
        get_name(transp_raw),
        ruc_transp,
        str(guide.get("license_plate") or ""),
        "",
        cod_producto, unidad, descripcion, cantidad, cant_bruta,
        get_name(guide.get("create_uid")),
        fmt_date(guide.get("date_start")),
        "", "", "", "",
    ]


# ── Google Sheets ─────────────────────────────────────────────────────────────
def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDS)
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)
    try:
        sheet = spreadsheet.worksheet(SHEET_TAB)
        log.info(f"Sheets: pestana '{SHEET_TAB}' encontrada")
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=SHEET_TAB, rows=10000, cols=27)
        log.info(f"Sheets: pestana '{SHEET_TAB}' creada")
    return sheet


def bulk_write(sheet, rows):
    """Escribe todas las filas en lotes — mucho mas rapido que una por una."""
    # Leer existentes para saber cuales actualizar
    log.info("Sheets: leyendo filas existentes...")
    existing_vals = sheet.get_all_values()

    # Asegurar header
    if not existing_vals or not existing_vals[0] or existing_vals[0][0] != "numero":
        sheet.insert_row(COLUMNS, index=1)
        existing_vals = [COLUMNS]
        log.info("Sheets: headers insertados")

    existing_map = {}
    for i, row in enumerate(existing_vals[1:], start=2):
        if row and row[0]:
            existing_map[row[0].strip()] = i

    new_rows = []
    update_data = []

    for row in rows:
        numero = row[0].strip() if row[0] else ""
        if not numero:
            continue
        if numero in existing_map:
            update_data.append((existing_map[numero], row))
        else:
            new_rows.append(row)

    log.info(f"Sheets: {len(new_rows)} nuevas, {len(update_data)} a actualizar")

    # Insertar nuevas en lotes
    if new_rows:
        for i in range(0, len(new_rows), SHEETS_BATCH):
            batch = new_rows[i:i+SHEETS_BATCH]
            sheet.append_rows(batch, value_input_option="USER_ENTERED")
            log.info(f"  Insertadas {min(i+SHEETS_BATCH, len(new_rows))}/{len(new_rows)}")
            time.sleep(1)

    # Actualizar existentes en lotes usando batch_update
    if update_data:
        for i in range(0, len(update_data), SHEETS_BATCH):
            batch = update_data[i:i+SHEETS_BATCH]
            col_end = "AA"
            updates = [{
                "range": f"A{idx}:{col_end}{idx}",
                "values": [row]
            } for idx, row in batch]
            sheet.batch_update(updates)
            log.info(f"  Actualizadas {min(i+SHEETS_BATCH, len(update_data))}/{len(update_data)}")
            time.sleep(1)

    return len(new_rows), len(update_data)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    start = datetime.now(timezone.utc)
    log.info("="*50)
    log.info("TEXCUMAR Sync — Inicio")
    log.info(f"UTC: {start.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("="*50)

    authenticate()

    # 1. Cargar guias en lotes
    guides = get_all_guides()
    if not guides:
        log.info("No hay guias para sincronizar.")
        return

    # 2. Recopilar IDs de lineas y partners para queries masivas
    all_line_ids = []
    all_partner_ids = []
    for g in guides:
        all_line_ids.extend(g.get("line_ids", []))
        for field in ("client_id", "partner_id"):
            v = g.get(field)
            if isinstance(v, (list, tuple)) and v:
                all_partner_ids.append(v[0])

    # 3. Cargar lineas y RUCs en queries masivas (no una por una)
    # Limitar line_ids a primeros 5000 para evitar timeout
    lines_map = get_all_lines(all_line_ids[:5000]) if all_line_ids else {}
    vats_map  = get_partner_vats(all_partner_ids)

    # 4. Transformar
    rows = []
    for g in guides:
        try:
            rows.append(transform(g, lines_map, vats_map))
        except Exception as e:
            log.warning(f"Error transformando {g.get('name','?')}: {e}")

    log.info(f"Transformadas {len(rows)} guias")

    # 5. Escribir en Sheets
    sheet = get_sheet()
    inserted, updated = bulk_write(sheet, rows)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    log.info("="*50)
    log.info(f"Sync completado en {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"  Nuevas:      {inserted}")
    log.info(f"  Actualizadas: {updated}")
    log.info("="*50)


main()
