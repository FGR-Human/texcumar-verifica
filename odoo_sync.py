#!/usr/bin/env python3
"""
odoo_sync.py — TEXCUMAR Remission Guide Sync
Fetches guides from Odoo → Google Sheets (pestaña 'Guias')

Campos extraídos: 26 columnas
  Encabezado (account.remission.guide):
    numero, autorizacion, fechaAutorizacion, base, codigoSCI, globalGAP,
    fechaInicio, fechaFin, destinatario, rucDestino, nombreDest, destino,
    llegada, motivo, transportista, rucTransp, placa, tecnico, despacho
  Producto (stock.move.line — JOIN por picking_id):
    codProducto, unidad, descripcion, cantidad, cantBruta
  Adicionales (stock.move.line):
    gavetas, plus, salinidad, temperatura
"""

import os
import sys
import json
import xmlrpc.client
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

SHEET_TAB     = "Guias"
BATCH_SIZE    = 200

# Columnas finales en el Sheet (en este orden exacto)
COLUMNS = [
    "numero", "autorizacion", "fechaAutorizacion", "base",
    "codigoSCI", "globalGAP", "fechaInicio", "fechaFin",
    "destinatario", "rucDestino", "nombreDest", "destino",
    "llegada", "motivo", "transportista", "rucTransp", "placa",
    "codProducto", "unidad", "descripcion", "cantidad", "cantBruta",
    "tecnico", "despacho", "gavetas", "plus",
]

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def fmt_date(val):
    """Convierte datetime/date de Odoo a string DD/MM/YYYY."""
    if not val or val is False:
        return ""
    if isinstance(val, str):
        # ISO: "2026-01-27 00:00:00" o "2026-01-27"
        try:
            d = datetime.strptime(val[:10], "%Y-%m-%d")
            return d.strftime("%d/%m/%Y")
        except Exception:
            return val
    return str(val)

def safe(val):
    """Limpia False/None de Odoo a string vacío."""
    if val is False or val is None:
        return ""
    if isinstance(val, (list, tuple)):
        # Odoo many2one retorna [id, name]
        return str(val[1]) if len(val) > 1 else str(val[0])
    return str(val).strip()

def safe_num(val):
    """Limpia número: convierte float .0 a int, False a ''."""
    if val is False or val is None:
        return ""
    try:
        f = float(val)
        return str(int(f)) if f == int(f) else str(f)
    except Exception:
        return str(val)

# ─── ODOO CONNECTION ─────────────────────────────────────────────────────────

def odoo_connect():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
    if not uid:
        raise SystemExit("❌ Error: autenticación Odoo fallida. Verifica ODOO_USER y ODOO_PASSWORD.")
    print(f"✅ Conectado a Odoo como UID {uid}")
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    return uid, models

def odoo_call(models, uid, model, method, args, kwargs=None):
    return models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        model, method, args, kwargs or {}
    )

# ─── FIELD DISCOVERY ─────────────────────────────────────────────────────────

def discover_fields(models, uid, model):
    """Retorna set de nombres de campos disponibles en el modelo."""
    try:
        fields = odoo_call(models, uid, model, "fields_get", [],
                           {"attributes": ["string", "type"]})
        return set(fields.keys())
    except Exception:
        return set()

# ─── FETCH GUIDES ────────────────────────────────────────────────────────────

# Posibles nombres de campos en Odoo según versión/localización
FIELD_MAP = {
    # Campo Sheet        : [opciones de nombre en Odoo, en orden de preferencia]
    "numero"            : ["name", "l10n_ec_name", "number"],
    "autorizacion"      : ["l10n_ec_authorization_number", "authorization_number", "l10n_latam_document_number"],
    "fechaAutorizacion" : ["l10n_ec_authorization_date", "invoice_date", "date_order", "date"],
    "base"              : ["location_dest_id", "warehouse_id", "origin", "note"],
    "codigoSCI"         : ["l10n_ec_sci_code", "sci_code", "ref"],
    "globalGAP"         : ["l10n_ec_global_gap", "global_gap_number", "x_global_gap"],
    "fechaInicio"       : ["scheduled_date", "date_done", "date", "sale_id.date_order"],
    "fechaFin"          : ["date_deadline", "date_done", "date"],
    "destinatario"      : ["partner_id", "dest_partner_id"],
    "rucDestino"        : ["partner_id.vat", "l10n_ec_ruc_dest"],
    "nombreDest"        : ["l10n_ec_dest_name", "dest_name", "partner_id.name"],
    "destino"           : ["l10n_ec_dest_address", "dest_address"],
    "llegada"           : ["l10n_ec_dest_point", "dest_point", "l10n_ec_arrival_point"],
    "motivo"            : ["l10n_ec_motive", "motive", "origin"],
    "transportista"     : ["carrier_id", "l10n_ec_carrier_id"],
    "rucTransp"         : ["l10n_ec_carrier_vat", "carrier_id.vat"],
    "placa"             : ["l10n_ec_plate", "plate", "x_plate"],
    "tecnico"           : ["l10n_ec_technician", "technician_id", "x_tecnico"],
    "despacho"          : ["l10n_ec_dispatch_type", "dispatch_type", "x_despacho"],
}

# Campos de producto — se buscan en stock.move.line o en las líneas de la guía
PRODUCT_FIELD_MAP = {
    "codProducto" : ["product_id.default_code", "product_id", "x_product_code"],
    "unidad"      : ["product_uom_id", "product_uom", "uom_id"],
    "descripcion" : ["product_id.name", "description", "product_id"],
    "cantidad"    : ["quantity", "qty_done", "product_uom_qty", "quantity_done"],
    "cantBruta"   : ["l10n_ec_gross_weight", "gross_weight", "x_cant_bruta", "weight"],
    "gavetas"     : ["l10n_ec_containers", "x_gavetas", "number_of_packages"],
    "plus"        : ["x_plus", "l10n_ec_plus"],
}


def get_guide_records(models, uid):
    """Obtiene todos los registros de guías de remisión de Odoo."""

    # ── Verificar modelo ────────────────────────────────────────────────────
    available = discover_fields(models, uid, "account.remission.guide")
    if not available:
        available = discover_fields(models, uid, "stock.picking")
        if available:
            print("⚠️  Usando stock.picking como modelo base")
            return get_from_stock_picking(models, uid)
        raise SystemExit("❌ No se encontró modelo de guías de remisión en Odoo.")

    print(f"   Campos disponibles en account.remission.guide: {len(available)}")

    # ── Contar sin filtro ───────────────────────────────────────────────────
    total_all = odoo_call(models, uid, "account.remission.guide", "search_count", [[]])
    print(f"   >> TOTAL registros sin filtro: {total_all}")

    if total_all == 0:
        raise SystemExit("❌ account.remission.guide está vacío. "
                         "Verifica permisos del usuario Odoo o que sea la DB correcta.")

    # ── Diagnóstico de estados ──────────────────────────────────────────────
    if "state" in available:
        from collections import Counter
        sample = odoo_call(models, uid, "account.remission.guide", "search_read",
                           [[]], {"fields": ["state"], "limit": 500})
        counts = Counter(str(r.get("state", "")) for r in sample)
        print(f"   >> Estados en muestra: {dict(counts)}")

    # ── Construir lista de campos ───────────────────────────────────────────
    simple_fields = []
    for alternatives in FIELD_MAP.values():
        for f in alternatives:
            base = f.split(".")[0]
            if base in available:
                simple_fields.append(base)
                break
    for must in ["name", "id", "picking_id", "move_ids", "move_line_ids",
                 "l10n_ec_remission_line_ids", "state"]:
        if must in available and must not in simple_fields:
            simple_fields.append(must)
    simple_fields = list(set(simple_fields))
    print(f"   Pidiendo {len(simple_fields)} campos")

    # ── Fetch SIN filtro de estado ──────────────────────────────────────────
    # El modelo account.remission.guide SOLO contiene guías de remisión
    # No necesitamos filtrar por estado — traemos todas
    all_records = []
    offset = 0
    while offset < total_all:
        batch = odoo_call(models, uid, "account.remission.guide", "search_read",
                          [[]],
                          {"fields": simple_fields, "limit": BATCH_SIZE,
                           "offset": offset, "order": "id asc"})
        if not batch:
            break
        all_records.extend(batch)
        offset += len(batch)
        print(f"   Descargadas {offset}/{total_all}...")

    return all_records, available


def get_from_stock_picking(models, uid):
    """Fallback: extrae desde stock.picking si account.remission.guide no existe."""
    available = discover_fields(models, uid, "stock.picking")
    # Descubrir estado real en stock.picking
    sp_total = odoo_call(models, uid, "stock.picking", "search_count", [[]])
    print(f"   stock.picking total sin filtro: {sp_total}")
    sp_domain = [("picking_type_code", "=", "outgoing")]
    if sp_total > 0:
        sp_sample = odoo_call(models, uid, "stock.picking", "search_read",
                              [[]], {"fields": ["state"], "limit": 100})
        sp_estados = list(set(r.get("state","") for r in sp_sample if r.get("state")))
        for pref in ["done", "validated", "authorized", "confirmed"]:
            if pref in sp_estados:
                sp_domain.append(("state", "=", pref))
                print(f"   stock.picking usando state='{pref}'")
                break
    domain = sp_domain
    total = odoo_call(models, uid, "stock.picking", "search_count", [domain])
    fields = [f for f in ["name", "id", "partner_id", "scheduled_date",
                           "date_done", "carrier_id", "origin", "note",
                           "move_line_ids", "move_ids"] if f in available]
    all_records = []
    offset = 0
    while offset < total:
        batch = odoo_call(models, uid, "stock.picking", "search_read",
                          [domain], {"fields": fields, "limit": BATCH_SIZE, "offset": offset})
        all_records.extend(batch)
        offset += BATCH_SIZE
        if not batch:
            break
    return all_records, available


# ─── FETCH PRODUCT LINES ─────────────────────────────────────────────────────

def get_move_lines(models, uid, picking_ids):
    """
    Obtiene líneas de movimiento (stock.move.line) para una lista de picking_ids.
    Retorna dict: {picking_id: [líneas]}
    """
    if not picking_ids:
        return {}

    available = discover_fields(models, uid, "stock.move.line")
    want = ["picking_id", "product_id", "product_uom_id", "quantity",
            "qty_done", "lot_id", "result_package_id",
            "l10n_ec_gross_weight", "weight"]
    fields = [f for f in want if f in available]

    print(f"   Descargando líneas de producto para {len(picking_ids)} guías...")
    lines = odoo_call(models, uid, "stock.move.line", "search_read",
                      [[("picking_id", "in", picking_ids)]],
                      {"fields": fields})

    by_picking = {}
    for line in lines:
        pid = line["picking_id"][0] if isinstance(line["picking_id"], list) else line["picking_id"]
        by_picking.setdefault(pid, []).append(line)
    return by_picking


def get_guide_lines(models, uid, guide_ids):
    """
    Intenta obtener líneas propias de la guía de remisión.
    Prueba varios modelos: l10n_ec.remission.guide.line, account.remission.guide.line
    """
    for line_model in ["l10n_ec.remission.guide.line", "account.remission.guide.line",
                        "stock.move"]:
        try:
            available = discover_fields(models, uid, line_model)
            if not available:
                continue
            # Buscar el campo que relaciona al guide
            guide_field = None
            for f in ["remission_guide_id", "guide_id", "l10n_ec_guide_id"]:
                if f in available:
                    guide_field = f
                    break
            if not guide_field:
                continue

            want = [guide_field, "product_id", "product_uom_id", "product_uom_qty",
                    "quantity", "qty_done", "name", "product_uom",
                    "l10n_ec_gross_weight", "x_cant_bruta"]
            fields = [f for f in want if f in available]

            lines = odoo_call(models, uid, line_model, "search_read",
                              [[(guide_field, "in", guide_ids)]],
                              {"fields": fields})

            by_guide = {}
            for line in lines:
                gid = line[guide_field]
                if isinstance(gid, list):
                    gid = gid[0]
                by_guide.setdefault(gid, []).append(line)

            if lines:
                print(f"   ✅ Líneas de producto obtenidas desde '{line_model}'")
                return by_guide, line_model
        except Exception:
            continue

    return {}, None


# ─── FIELD EXTRACTION ────────────────────────────────────────────────────────

def extract_field(record, candidates, default=""):
    """Extrae el primer campo disponible de una lista de candidatos."""
    for field in candidates:
        val = record.get(field)
        if val is not False and val is not None and val != "":
            return val
    return default


def build_row(guide, product_line, available_fields):
    """Construye una fila de 26 columnas para el Sheet."""

    def g(candidates):
        return extract_field(guide, candidates)

    def p(candidates):
        return extract_field(product_line, candidates) if product_line else ""

    # ── Encabezado ──
    numero           = safe(g(["name", "l10n_ec_name", "number"]))
    autorizacion     = safe(g(["l10n_ec_authorization_number", "authorization_number",
                                "l10n_latam_document_number"]))
    fechaAutorizacion= fmt_date(g(["l10n_ec_authorization_date", "invoice_date",
                                   "date_order", "date"]))
    base             = safe(g(["location_dest_id", "warehouse_id", "origin_location",
                                "location_id", "note", "origin"]))
    codigoSCI        = safe(g(["l10n_ec_sci_code", "sci_code", "ref", "x_sci"]))
    globalGAP        = safe(g(["l10n_ec_global_gap", "global_gap_number", "x_global_gap"]))
    fechaInicio      = fmt_date(g(["scheduled_date", "date_order", "date"]))
    fechaFin         = fmt_date(g(["date_deadline", "date_done"]))
    destinatario     = safe(g(["partner_id", "dest_partner_id"]))
    rucDestino       = safe(g(["l10n_ec_ruc_dest", "x_ruc_dest", "partner_vat"]))
    nombreDest       = safe(g(["l10n_ec_dest_name", "dest_name", "x_nombre_dest"]))
    destino          = safe(g(["l10n_ec_dest_address", "dest_address", "x_destino"]))
    llegada          = safe(g(["l10n_ec_dest_point", "dest_point", "arrival_point",
                                "x_llegada"]))
    motivo           = safe(g(["l10n_ec_motive", "motive", "x_motivo", "origin"]))
    transportista    = safe(g(["carrier_id", "l10n_ec_carrier_id", "x_transportista"]))
    rucTransp        = safe(g(["l10n_ec_carrier_vat", "carrier_vat", "x_ruc_transp"]))
    placa            = safe(g(["l10n_ec_plate", "plate", "x_plate", "x_placa"]))
    tecnico          = safe(g(["l10n_ec_technician", "x_tecnico", "technician"]))
    despacho         = safe(g(["l10n_ec_dispatch_type", "dispatch_type", "x_despacho"]))

    # ── Producto (desde línea) ──
    codProducto = ""
    unidad      = ""
    descripcion = ""
    cantidad    = ""
    cantBruta   = ""
    gavetas     = ""
    plus        = ""

    if product_line:
        # Código de producto
        prod = product_line.get("product_id")
        if prod and prod is not False:
            if isinstance(prod, list):
                descripcion = safe(prod[1]) if len(prod) > 1 else ""
                # default_code viene en otro campo
                codProducto = safe(product_line.get("product_default_code", ""))
                if not codProducto:
                    codProducto = safe(product_line.get("default_code", ""))
            else:
                descripcion = safe(prod)

        # Si hay campo explícito de descripcion
        for df in ["name", "description", "product_name"]:
            v = product_line.get(df)
            if v and v is not False:
                descripcion = safe(v)
                break

        # Unidad
        uom = product_line.get("product_uom_id") or product_line.get("product_uom")
        if uom and uom is not False:
            unidad = safe(uom)

        # Cantidades
        for qf in ["product_uom_qty", "quantity", "qty_done", "quantity_done"]:
            v = product_line.get(qf)
            if v and v is not False and v != 0:
                cantidad = safe_num(v)
                break

        cantBruta = safe_num(
            product_line.get("l10n_ec_gross_weight") or
            product_line.get("gross_weight") or
            product_line.get("x_cant_bruta") or
            product_line.get("weight") or ""
        )

        gavetas = safe_num(
            product_line.get("l10n_ec_containers") or
            product_line.get("x_gavetas") or
            product_line.get("number_of_packages") or ""
        )
        plus = safe_num(product_line.get("x_plus") or "")

    return [
        numero, autorizacion, fechaAutorizacion, base,
        codigoSCI, globalGAP, fechaInicio, fechaFin,
        destinatario, rucDestino, nombreDest, destino,
        llegada, motivo, transportista, rucTransp, placa,
        codProducto, unidad, descripcion, cantidad, cantBruta,
        tecnico, despacho, gavetas, plus,
    ]


# ─── GOOGLE SHEETS ───────────────────────────────────────────────────────────

def get_sheet():
    creds_dict = json.loads(CREDS_JSON)
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)

    # Obtener o crear pestaña Guias
    try:
        ws = sh.worksheet(SHEET_TAB)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_TAB, rows=10000, cols=len(COLUMNS))
        print(f"✅ Pestaña '{SHEET_TAB}' creada")

    return ws


def write_to_sheet(ws, rows):
    """Limpia la pestaña y escribe todos los datos de una vez."""
    ws.clear()
    all_data = [COLUMNS] + rows
    ws.update(all_data, value_input_option="RAW")
    print(f"✅ {len(rows)} registros escritos en '{SHEET_TAB}'")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("TEXCUMAR — Sync Odoo → Google Sheets")
    print(f"Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. Conectar Odoo
    uid, models = odoo_connect()

    # 2. Obtener guías
    print("\n📥 Descargando guías de remisión...")
    guide_records, available_fields = get_guide_records(models, uid)
    print(f"   Total descargadas: {len(guide_records)}")

    if not guide_records:
        print("⚠️  Sin registros. Revisa el dominio de búsqueda o las credenciales.")
        return

    # 3. Obtener líneas de producto
    print("\n📦 Descargando líneas de producto...")
    guide_ids = [r["id"] for r in guide_records]

    # Intentar primero modelo de líneas de guía
    lines_by_guide, line_model_used = get_guide_lines(models, uid, guide_ids)

    # Si no funcionó, intentar via stock.move.line con picking_id
    if not lines_by_guide:
        print("   Probando via stock.move.line...")
        picking_ids = []
        for r in guide_records:
            pid = r.get("picking_id")
            if pid and pid is not False:
                if isinstance(pid, list):
                    picking_ids.append(pid[0])
                else:
                    picking_ids.append(pid)
            # Algunos modelos tienen move_ids directamente
            mid = r.get("move_ids")
            if mid and isinstance(mid, list):
                picking_ids.extend(mid)

        if picking_ids:
            move_lines = get_move_lines(models, uid, list(set(picking_ids)))
            # Remap por guide_id usando picking_id del record
            for r in guide_records:
                pid = r.get("picking_id")
                if pid:
                    pid = pid[0] if isinstance(pid, list) else pid
                    if pid in move_lines:
                        lines_by_guide[r["id"]] = move_lines[pid]
        else:
            print("   ⚠️  No se encontró campo picking_id ni move_ids en las guías")
            print("   Los campos de producto aparecerán vacíos para registros nuevos")

    # Estadísticas
    with_product = sum(1 for gid in [r["id"] for r in guide_records] if gid in lines_by_guide)
    print(f"   Guías con datos de producto: {with_product}/{len(guide_records)}")

    # 4. Construir filas
    print("\n🔨 Construyendo filas...")
    sheet_rows = []
    for guide in guide_records:
        gid = guide["id"]
        # Tomar solo la primera línea de producto si hay varias
        product_lines = lines_by_guide.get(gid, [])
        product_line = product_lines[0] if product_lines else None
        row = build_row(guide, product_line, available_fields)
        sheet_rows.append(row)

    # 5. Escribir al Sheet
    print(f"\n📤 Escribiendo {len(sheet_rows)} registros al Sheet...")
    ws = get_sheet()
    write_to_sheet(ws, sheet_rows)

    print("\n" + "=" * 60)
    print(f"✅ Sync completado: {len(sheet_rows)} guías")
    print(f"   Con producto: {with_product} | Sin producto: {len(sheet_rows) - with_product}")
    if line_model_used:
        print(f"   Fuente de líneas: {line_model_used}")
    print(f"Fin: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
