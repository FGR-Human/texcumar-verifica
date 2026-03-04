import os
import json
import logging
import urllib.request
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

# 27 columnas A-AA
COLUMNS = [
    "numero",            # A  001-002-000013362
    "autorizacion",      # B  numero autorizacion SRI
    "fechaAutorizacion", # C  fecha autorizacion
    "base",              # D  almacen origen
    "codigoSCI",         # E  (vacio - campo manual TEXCUMAR)
    "globalGAP",         # F  (vacio - campo manual TEXCUMAR)
    "destinatario",      # G  nombre cliente
    "rucDestino",        # H  RUC cliente
    "nombreDest",        # I  nombre destino
    "destino",           # J  ciudad destino
    "llegada",           # K  fecha fin finalizacion
    "motivo",            # L  observacion
    "transportista",     # M  nombre transportista
    "rucTransp",         # N  RUC transportista
    "placa",             # O  license_plate
    "partida",           # P  direccion partida
    "codProducto",       # Q  codigo producto
    "unidad",            # R  unidad medida
    "descripcion",       # S  nombre producto
    "cantidad",          # T  cantidad
    "cantBruta",         # U  cantidad bruta
    "tecnico",           # V  creado por
    "despacho",          # W  date_start
    "gavetas",           # X  (vacio - campo manual TEXCUMAR)
    "plus",              # Y  (vacio - campo manual TEXCUMAR)
    "salinidad",         # Z  (vacio - campo manual TEXCUMAR)
    "temperatura",       # AA (vacio - campo manual TEXCUMAR)
]

SESSION_COOKIE = None


# ── Odoo RPC ─────────────────────────────────────────────────────────────────
def rpc(endpoint, params, retries=3):
    global SESSION_COOKIE
    url = f"{ODOO_URL}{endpoint}"
    data = json.dumps({"jsonrpc": "2.0", "method": "call", "id": 1, "params": params}).encode()
    headers = {"Content-Type": "application/json"}
    if SESSION_COOKIE:
        headers["Cookie"] = SESSION_COOKIE

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                if not SESSION_COOKIE:
                    raw = r.headers.get("Set-Cookie", "")
                    if raw:
                        SESSION_COOKIE = raw.split(";")[0]
                resp = json.loads(r.read())
            if "error" in resp:
                raise RuntimeError(json.dumps(resp["error"]))
            return resp["result"]
        except Exception as e:
            if attempt < retries - 1:
                log.warning(f"RPC intento {attempt+1} fallo: {e}. Reintentando...")
            else:
                raise


def authenticate():
    result = rpc("/web/session/authenticate", {
        "db": ODOO_DB,
        "login": ODOO_USER,
        "password": ODOO_PASSWORD
    })
    uid = result.get("uid")
    if not uid:
        raise RuntimeError(f"Autenticacion fallida: {result}")
    log.info(f"Odoo: autenticado UID={uid}")
    return uid


def search_read(model, domain, fields, limit=0):
    return rpc("/web/dataset/call_kw", {
        "model": model,
        "method": "search_read",
        "args": [domain],
        "kwargs": {"fields": fields, "limit": limit, "order": "id asc"}
    })


# ── Helpers ──────────────────────────────────────────────────────────────────
def get_name(field_val):
    if isinstance(field_val, (list, tuple)) and len(field_val) >= 2:
        return str(field_val[1])
    if isinstance(field_val, str):
        return field_val
    return ""


def fmt_date(val):
    if not val:
        return ""
    s = str(val)[:10]
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return s


# ── Obtener guias de Odoo ────────────────────────────────────────────────────
def get_guides():
    log.info("Odoo: consultando guias publicadas...")
    guides = search_read(
        "account.remission.guide",
        [["state", "=", "posted"]],
        [
            "l10n_latam_document_number",  # numero limpio 001-002-000013362
            "name",                         # fallback
            "date_start",                   # fecha despacho
            "date_end_finalization",         # fecha llegada/fin
            "license_plate",                # placa
            "partner_id",                   # TRANSPORTISTA
            "client_id",                    # DESTINATARIO
            "warehouse_id",                 # almacen origen
            "observation",                  # observacion/motivo
            "line_ids",                     # lineas de producto
            "l10n_ec_authorization_number", # autorizacion SRI
            "l10n_ec_authorization_date",   # fecha autorizacion SRI
            "create_uid",                   # creado por (tecnico)
            "state",
        ]
    )
    log.info(f"Odoo: {len(guides)} guias encontradas")
    return guides


def get_lines(line_ids):
    if not line_ids:
        return []
    return search_read(
        "account.remission.guide.line",
        [["id", "in", line_ids]],
        ["product_id", "product_uom_id", "product_qty", "qty_done", "name"]
    )


def get_partner_ruc(partner_id):
    if not partner_id:
        return ""
    pid = partner_id[0] if isinstance(partner_id, (list, tuple)) else partner_id
    result = search_read(
        "res.partner",
        [["id", "=", pid]],
        ["vat"],
        limit=1
    )
    if result:
        return result[0].get("vat") or ""
    return ""


# ── Transformar guia → fila ──────────────────────────────────────────────────
def transform(guide, lines, ruc_cliente, ruc_transp):
    # Numero limpio
    numero = (
        guide.get("l10n_latam_document_number") or
        get_name(guide.get("name", "")).replace("REM ", "").strip()
    )

    # Producto (primera linea)
    descripcion = ""
    cod_producto = ""
    unidad = ""
    cantidad = ""
    cant_bruta = ""
    if lines:
        l = lines[0]
        prod = l.get("product_id", False)
        descripcion = get_name(prod)
        cod_producto = str(prod[0]) if isinstance(prod, (list, tuple)) else ""
        uom = l.get("product_uom_id", False)
        unidad = get_name(uom)
        qty = l.get("qty_done") or l.get("product_qty") or 0
        cantidad = str(int(qty)) if qty else ""
        cant_bruta = cantidad

    row = [
        numero,                                              # A numero
        str(guide.get("l10n_ec_authorization_number") or ""), # B autorizacion
        fmt_date(guide.get("l10n_ec_authorization_date")),  # C fechaAutorizacion
        get_name(guide.get("warehouse_id")),                 # D base
        "",                                                  # E codigoSCI
        "",                                                  # F globalGAP
        get_name(guide.get("client_id")),                    # G destinatario
        ruc_cliente,                                         # H rucDestino
        get_name(guide.get("client_id")),                    # I nombreDest
        "",                                                  # J destino
        fmt_date(guide.get("date_end_finalization")),        # K llegada
        str(guide.get("observation") or ""),                 # L motivo
        get_name(guide.get("partner_id")),                   # M transportista
        ruc_transp,                                          # N rucTransp
        str(guide.get("license_plate") or ""),               # O placa
        "",                                                  # P partida
        cod_producto,                                        # Q codProducto
        unidad,                                              # R unidad
        descripcion,                                         # S descripcion
        cantidad,                                            # T cantidad
        cant_bruta,                                          # U cantBruta
        get_name(guide.get("create_uid")),                   # V tecnico
        fmt_date(guide.get("date_start")),                   # W despacho
        "",                                                  # X gavetas
        "",                                                  # Y plus
        "",                                                  # Z salinidad
        "",                                                  # AA temperatura
    ]
    return row


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
        sheet = spreadsheet.add_worksheet(title=SHEET_TAB, rows=2000, cols=27)
        log.info(f"Sheets: pestana '{SHEET_TAB}' creada")
    return sheet


def ensure_header(sheet):
    first_row = sheet.row_values(1)
    if not first_row or first_row[0] != "numero":
        sheet.insert_row(COLUMNS, index=1)
        log.info("Sheets: headers insertados")


def upsert(sheet, rows):
    ensure_header(sheet)
    all_vals = sheet.get_all_values()
    existing = {}
    for i, row in enumerate(all_vals[1:], start=2):
        if row and row[0]:
            existing[row[0].strip()] = i

    inserted = updated = skipped = 0
    for row in rows:
        numero = row[0].strip() if row[0] else ""
        if not numero:
            skipped += 1
            continue
        if numero in existing:
            col_end = chr(ord("A") + len(row) - 1) if len(row) <= 26 else "AA"
            idx = existing[numero]
            sheet.update(f"A{idx}:{col_end}{idx}", [row])
            updated += 1
        else:
            sheet.append_row(row, value_input_option="USER_ENTERED")
            inserted += 1
            log.info(f"  + {numero}")

    return inserted, updated, skipped


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    start = datetime.now(timezone.utc)
    log.info("=" * 50)
    log.info("TEXCUMAR Sync — Inicio")
    log.info(f"UTC: {start.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 50)

    authenticate()
    guides = get_guides()

    if not guides:
        log.info("No hay guias para sincronizar.")
        return

    sheet = get_sheet()
    rows = []

    for guide in guides:
        try:
            line_ids = guide.get("line_ids", [])
            lines = get_lines(line_ids)
            ruc_cliente = get_partner_ruc(guide.get("client_id"))
            ruc_transp  = get_partner_ruc(guide.get("partner_id"))
            row = transform(guide, lines, ruc_cliente, ruc_transp)
            rows.append(row)
        except Exception as e:
            log.warning(f"Error en guia {guide.get('name','?')}: {e}")
            continue

    log.info(f"Transformadas {len(rows)} guias")
    inserted, updated, skipped = upsert(sheet, rows)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    log.info("=" * 50)
    log.info(f"Sync completado en {elapsed:.1f}s")
    log.info(f"  Insertadas:  {inserted}")
    log.info(f"  Actualizadas: {updated}")
    log.info(f"  Omitidas:    {skipped}")
    log.info("=" * 50)


main()
