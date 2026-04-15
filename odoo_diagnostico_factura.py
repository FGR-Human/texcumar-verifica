#!/usr/bin/env python3
"""
odoo_diagnostico_factura.py — TEXCUMAR
Encuentra el campo que conecta account.remission.guide con el número de factura.

Estrategia: prueba 4 rutas posibles en Odoo para llegar a account.move (facturas):
  Ruta A: campo directo en la guía (invoice_id, move_id, invoice_ids, etc.)
  Ruta B: guía → stock.picking → sale.order → account.move
  Ruta C: guía → línea de guía → picking → factura
  Ruta D: buscar en account.move el campo que referencia la guía

El log imprime EXACTAMENTE qué campo usar y el número de factura de ejemplo.
"""

import os, sys, xmlrpc.client
from datetime import datetime

ODOO_URL      = os.environ["ODOO_URL"].rstrip("/")
ODOO_DB       = os.environ["ODOO_DB"]
ODOO_USER     = os.environ["ODOO_USER"]
ODOO_PASSWORD = os.environ["ODOO_PASSWORD"]

# ─── CONEXIÓN ────────────────────────────────────────────────────────────────
def connect():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
    uid    = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
    if not uid:
        sys.exit("❌ Autenticación fallida")
    print(f"✅ Conectado — UID {uid} | DB: {ODOO_DB}")
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
    return uid, models

def rpc(models, uid, model, method, args, kw=None):
    return models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, model, method, args, kw or {})

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print(f"DIAGNÓSTICO FACTURA ↔ GUÍA — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    uid, models = connect()

    # ── Tomar muestra de guías recientes (con picking) ────────────────────────
    print("\n📥 Cargando muestra de guías recientes (estado=posted)...")
    guias = rpc(models, uid, "account.remission.guide", "search_read",
                [[["state", "=", "posted"]]],
                {"fields": ["id", "name", "l10n_latam_document_number",
                            "line_ids", "picking_id",
                            "invoice_ids", "invoice_id",
                            "account_move_ids", "move_id",
                            "sale_id", "origin"],
                 "limit": 5, "order": "id desc"})

    if not guias:
        sys.exit("❌ Sin guías en estado posted")

    print(f"   {len(guias)} guías cargadas\n")

    # ── Obtener todos los campos del modelo para saber cuáles existen ─────────
    print("📋 Obteniendo campos del modelo account.remission.guide...")
    all_fields = rpc(models, uid, "account.remission.guide", "fields_get", [],
                     {"attributes": ["string", "type", "relation"]})

    # Filtrar campos que podrían apuntar a facturas
    invoice_candidates = []
    for fname, fmeta in all_fields.items():
        name_lower = fname.lower()
        rel = fmeta.get("relation", "")
        if any(kw in name_lower for kw in ["invoice", "move", "factura", "sale", "origin"]):
            invoice_candidates.append((fname, fmeta["type"], rel, fmeta.get("string", "")))

    print(f"\n🔍 Campos candidatos en account.remission.guide relacionados con facturas:")
    print(f"   {'Campo':<40} {'Tipo':<15} {'Relación':<30} Etiqueta")
    print("   " + "-" * 100)
    for fname, ftype, rel, label in sorted(invoice_candidates):
        print(f"   {fname:<40} {ftype:<15} {rel:<30} {label}")

    # ── RUTA A: campo directo en la guía ──────────────────────────────────────
    print(f"\n{'='*70}")
    print("RUTA A — Campos directos de factura en la guía")
    print("="*70)

    direct_invoice_fields = [
        "invoice_ids", "invoice_id", "account_move_ids", "move_id",
        "l10n_ec_invoice_id", "related_invoice_id",
    ]

    ruta_a_encontrada = False
    for guia in guias:
        gnum = guia.get("l10n_latam_document_number") or guia.get("name")
        print(f"\n  Guía: {gnum} (id={guia['id']})")
        for field in direct_invoice_fields:
            if field not in all_fields:
                continue
            val = guia.get(field)
            if val and val is not False and val != []:
                print(f"  ✅ CAMPO ENCONTRADO: '{field}' = {repr(val)}")
                # Intentar leer el número de factura
                try:
                    if isinstance(val, list) and len(val) > 0:
                        # one2many o many2many → lista de IDs
                        inv_ids = val if isinstance(val[0], int) else [v[0] for v in val]
                        invoices = rpc(models, uid, "account.move", "read",
                                       [inv_ids[:3]],
                                       {"fields": ["name", "l10n_latam_document_number",
                                                   "move_type", "state"]})
                        for inv in invoices:
                            print(f"     → Factura: {inv.get('l10n_latam_document_number') or inv.get('name')} | tipo={inv.get('move_type')} | estado={inv.get('state')}")
                        ruta_a_encontrada = True
                    elif isinstance(val, list) and len(val) == 2:
                        # many2one → [id, name]
                        inv_id = val[0]
                        invoice = rpc(models, uid, "account.move", "read",
                                      [[inv_id]],
                                      {"fields": ["name", "l10n_latam_document_number",
                                                  "move_type", "state"]})[0]
                        print(f"     → Factura: {invoice.get('l10n_latam_document_number') or invoice.get('name')} | tipo={invoice.get('move_type')} | estado={invoice.get('state')}")
                        ruta_a_encontrada = True
                except Exception as e:
                    print(f"     ⚠️  Error leyendo factura: {e}")
            else:
                print(f"  ⬜ '{field}': vacío o no existe")

    # ── RUTA B: guía → picking → sale → factura ───────────────────────────────
    print(f"\n{'='*70}")
    print("RUTA B — Guía → stock.picking → sale.order → account.move")
    print("="*70)

    ruta_b_encontrada = False
    for guia in guias[:3]:
        gnum = guia.get("l10n_latam_document_number") or guia.get("name")
        print(f"\n  Guía: {gnum} (id={guia['id']})")

        # Obtener líneas de guía para encontrar picking_id
        lines = rpc(models, uid, "account.remission.guide.line", "search_read",
                    [[["guide_id", "=", guia["id"]]]],
                    {"fields": ["id", "picking_id"], "limit": 1})

        picking_id = None
        if guia.get("picking_id") and guia["picking_id"] is not False:
            picking_id = guia["picking_id"][0] if isinstance(guia["picking_id"], list) else guia["picking_id"]
            print(f"  📦 picking_id (directo en guía): {picking_id}")
        elif lines and lines[0].get("picking_id"):
            picking_id = lines[0]["picking_id"][0] if isinstance(lines[0]["picking_id"], list) else lines[0]["picking_id"]
            print(f"  📦 picking_id (desde línea): {picking_id}")
        else:
            print(f"  ⬜ Sin picking_id")
            continue

        # Leer el picking
        try:
            picking = rpc(models, uid, "stock.picking", "read",
                          [[picking_id]],
                          {"fields": ["name", "sale_id", "origin",
                                      "purchase_id", "invoice_ids"]})[0]
            print(f"  📦 Picking: {picking.get('name')} | origin={picking.get('origin')}")

            # Intentar via sale_id
            if picking.get("sale_id") and picking["sale_id"] is not False:
                sale_id = picking["sale_id"][0]
                print(f"  🛒 sale_id: {picking['sale_id']}")
                # Buscar facturas del pedido de venta
                invoices = rpc(models, uid, "account.move", "search_read",
                               [[["invoice_origin", "like", picking.get("origin", "")],
                                 ["move_type", "in", ["out_invoice", "out_refund"]],
                                 ["state", "!=", "cancel"]]],
                               {"fields": ["name", "l10n_latam_document_number",
                                           "move_type", "state", "invoice_origin"],
                                "limit": 3})
                if invoices:
                    for inv in invoices:
                        num = inv.get("l10n_latam_document_number") or inv.get("name")
                        print(f"  ✅ FACTURA VIA SALE: {num} | origen={inv.get('invoice_origin')}")
                        ruta_b_encontrada = True
                else:
                    # Buscar directamente en la venta
                    sale = rpc(models, uid, "sale.order", "read",
                               [[sale_id]],
                               {"fields": ["name", "invoice_ids"]})[0]
                    if sale.get("invoice_ids"):
                        inv_ids = sale["invoice_ids"]
                        invoices2 = rpc(models, uid, "account.move", "read",
                                        [inv_ids[:3]],
                                        {"fields": ["name", "l10n_latam_document_number",
                                                    "move_type", "state"]})
                        for inv in invoices2:
                            num = inv.get("l10n_latam_document_number") or inv.get("name")
                            print(f"  ✅ FACTURA VIA SALE.INVOICE_IDS: {num}")
                            ruta_b_encontrada = True

            # Intentar via origin directo en account.move
            origin = picking.get("origin", "")
            if origin:
                invoices = rpc(models, uid, "account.move", "search_read",
                               [[["invoice_origin", "=", origin],
                                 ["move_type", "in", ["out_invoice", "out_refund"]]]],
                               {"fields": ["name", "l10n_latam_document_number",
                                           "move_type", "state"],
                                "limit": 3})
                if invoices:
                    for inv in invoices:
                        num = inv.get("l10n_latam_document_number") or inv.get("name")
                        print(f"  ✅ FACTURA VIA ORIGIN '{origin}': {num}")
                        ruta_b_encontrada = True

        except Exception as e:
            print(f"  ⚠️  Error en ruta B: {e}")

    # ── RUTA C: buscar en account.move campos que apunten a la guía ───────────
    print(f"\n{'='*70}")
    print("RUTA C — Buscar en account.move campos que referencien la guía")
    print("="*70)

    try:
        move_fields = rpc(models, uid, "account.move", "fields_get", [],
                          {"attributes": ["string", "type", "relation"]})
        guide_refs = [(f, m) for f, m in move_fields.items()
                      if m.get("relation") == "account.remission.guide"
                      or "remission" in f.lower() or "guide" in f.lower() or "guia" in f.lower()]
        if guide_refs:
            print(f"  ✅ Campos en account.move que referencian la guía:")
            for fname, fmeta in guide_refs:
                print(f"     {fname} ({fmeta['type']}) → {fmeta.get('relation','')} — {fmeta.get('string','')}")
        else:
            print(f"  ⬜ account.move no tiene campos directos hacia la guía")
    except Exception as e:
        print(f"  ⚠️  Error explorando account.move: {e}")

    # ── RESUMEN FINAL ─────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("RESUMEN Y SIGUIENTE PASO")
    print("="*70)
    if ruta_a_encontrada:
        print("✅ RUTA A funcionó — hay un campo directo en la guía.")
        print("   → Usar ese campo en odoo_sync.py para agregar numFactura")
    elif ruta_b_encontrada:
        print("✅ RUTA B funcionó — la factura se obtiene via picking → sale order.")
        print("   → Se puede agregar la lógica al sync pero requiere un fetch extra")
    else:
        print("⚠️  Ninguna ruta encontró la factura automáticamente.")
        print("   → Comparte este log completo para analizar la siguiente estrategia.")
    print("="*70)

if __name__ == "__main__":
    main()
