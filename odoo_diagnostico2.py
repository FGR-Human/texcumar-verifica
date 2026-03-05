#!/usr/bin/env python3
"""
odoo_diagnostico2.py — TEXCUMAR
Segunda pasada: inspecciona en profundidad
  1. Todos los campos de account.remission.guide.line
  2. Todos los campos de stock.move.line para ese picking
  3. Todos los campos de stock.move para ese picking
  4. Partner del CLIENT (client_id) para rucDestino, destino, llegada
  5. remission.information.sri (other_inf_ids)
  6. Muestra 5 guías distintas con sus líneas para ver variedad
"""
import os, sys, xmlrpc.client
from datetime import datetime

ODOO_URL      = os.environ["ODOO_URL"].rstrip("/")
ODOO_DB       = os.environ["ODOO_DB"]
ODOO_USER     = os.environ["ODOO_USER"]
ODOO_PASSWORD = os.environ["ODOO_PASSWORD"]

def main():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    uid    = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    def call(model, method, args, kw=None):
        return models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, model, method, args, kw or {})

    def dump_record(rec, label=""):
        print(f"\n{'─'*60}")
        print(f"{label}")
        print(f"{'─'*60}")
        for k in sorted(rec.keys()):
            v = rec[k]
            if v is not False and v is not None and v != "" and v != []:
                print(f"  {k:<45}: {repr(v)[:100]}")
        print(f"  --- VACÍOS ---")
        for k in sorted(rec.keys()):
            v = rec[k]
            if v is False or v is None or v == "" or v == []:
                print(f"  {k:<45}: (vacío)")

    print("=" * 70)
    print(f"DIAGNÓSTICO 2 — {datetime.now()}")
    print("=" * 70)

    # ── 1. TODOS los campos de account.remission.guide.line ─────────────────
    print("\n\n══════════════════════════════════════════════════════════════════════")
    print("1. MODELO: account.remission.guide.line — TODOS SUS CAMPOS")
    print("══════════════════════════════════════════════════════════════════════")
    lf = call("account.remission.guide.line", "fields_get", [],
              {"attributes": ["string", "type", "relation"]})
    print(f"Total campos: {len(lf)}\n")
    print(f"  {'Campo':<45} {'Tipo':<15} {'Relación':<30} Etiqueta")
    print("  " + "-"*100)
    for fn in sorted(lf.keys()):
        fm = lf[fn]
        print(f"  {fn:<45} {fm['type']:<15} {fm.get('relation',''):<30} {fm.get('string','')}")

    # Fetch primer registro completo
    line1 = call("account.remission.guide.line", "search_read",
                 [[("guide_id", "=", 1)]],
                 {"fields": list(lf.keys()), "limit": 1})
    if line1:
        dump_record(line1[0], "PRIMERA LÍNEA (guide_id=1) — TODOS LOS CAMPOS")
    else:
        print("⚠️  Sin líneas para guide_id=1")

    # Fetch 5 líneas distintas para ver variedad
    print("\n\n── 5 LÍNEAS ALEATORIAS (para ver variedad de datos) ──")
    lines5 = call("account.remission.guide.line", "search_read",
                  [[]], {"fields": list(lf.keys()), "limit": 5, "order": "id desc"})
    for i, ln in enumerate(lines5, 1):
        print(f"\n  Línea {i} (id={ln.get('id')}, guide={ln.get('guide_id')}):")
        for k in sorted(ln.keys()):
            v = ln[k]
            if v is not False and v is not None and v != "" and v != []:
                print(f"    {k:<43}: {repr(v)[:90]}")

    # ── 2. remission.information.sri (other_inf_ids) ────────────────────────
    print("\n\n══════════════════════════════════════════════════════════════════════")
    print("2. MODELO: remission.information.sri (other_inf_ids) — TODOS SUS CAMPOS")
    print("══════════════════════════════════════════════════════════════════════")
    try:
        sri_fields = call("remission.information.sri", "fields_get", [],
                          {"attributes": ["string", "type", "relation"]})
        print(f"Total campos: {len(sri_fields)}")
        for fn in sorted(sri_fields.keys()):
            fm = sri_fields[fn]
            print(f"  {fn:<45} {fm['type']:<15} {fm.get('relation',''):<30} {fm.get('string','')}")

        sri1 = call("remission.information.sri", "search_read",
                    [[("remission_guide_id", "=", 1)]],
                    {"fields": list(sri_fields.keys()), "limit": 1})
        if sri1:
            dump_record(sri1[0], "PRIMER registro remission.information.sri para guide_id=1")
        # Try with different link field
        sri_all5 = call("remission.information.sri", "search_read",
                        [[]], {"fields": list(sri_fields.keys()), "limit": 5})
        print(f"\nTotal registros (sin filtro, limit 5): {len(sri_all5)}")
        for i, sr in enumerate(sri_all5, 1):
            print(f"\n  Sri {i}:")
            for k,v in sr.items():
                if v is not False and v is not None and v != "" and v != []:
                    print(f"    {k}: {repr(v)[:90]}")
    except Exception as e:
        print(f"❌ Error: {e}")

    # ── 3. stock.move.line para picking_id=1 ────────────────────────────────
    print("\n\n══════════════════════════════════════════════════════════════════════")
    print("3. stock.move.line para picking_id=1")
    print("══════════════════════════════════════════════════════════════════════")
    sml_fields = call("stock.move.line", "fields_get", [],
                      {"attributes": ["string", "type", "relation"]})
    print(f"Total campos stock.move.line: {len(sml_fields)}")

    sml_recs = call("stock.move.line", "search_read",
                    [[("picking_id", "=", 1)]],
                    {"fields": list(sml_fields.keys())})
    print(f"Registros para picking_id=1: {len(sml_recs)}")
    if sml_recs:
        dump_record(sml_recs[0], "PRIMERA stock.move.line (picking_id=1)")
    else:
        # try with id=1 directly
        sml_recs = call("stock.move.line", "search_read",
                        [[]], {"fields": list(sml_fields.keys()), "limit": 1})
        if sml_recs:
            dump_record(sml_recs[0], "PRIMERA stock.move.line (sin filtro)")

    # ── 4. stock.move para picking_id=1 ─────────────────────────────────────
    print("\n\n══════════════════════════════════════════════════════════════════════")
    print("4. stock.move para picking_id=1")
    print("══════════════════════════════════════════════════════════════════════")
    sm_fields = call("stock.move", "fields_get", [],
                     {"attributes": ["string", "type", "relation"]})
    sm_recs = call("stock.move", "search_read",
                   [[("picking_id", "=", 1)]],
                   {"fields": list(sm_fields.keys())})
    print(f"Registros: {len(sm_recs)}")
    if sm_recs:
        dump_record(sm_recs[0], "PRIMER stock.move (picking_id=1)")

    # ── 5. CLIENT partner (client_id=3609) ───────────────────────────────────
    print("\n\n══════════════════════════════════════════════════════════════════════")
    print("5. res.partner client_id=3609 — CAMPOS RELEVANTES")
    print("══════════════════════════════════════════════════════════════════════")
    pf = call("res.partner", "fields_get", [], {"attributes": ["string","type","relation"]})
    p_rec = call("res.partner", "read", [[3609]], {"fields": list(pf.keys())})[0]
    # Show only non-empty non-binary
    print(f"  Partner id=3609:")
    for k in sorted(p_rec.keys()):
        v = p_rec[k]
        ft = pf.get(k, {}).get("type","")
        if ft == "binary":
            continue
        if v is not False and v is not None and v != "" and v != []:
            print(f"  {k:<45}: {repr(v)[:100]}")

    # ── 6. CARRIER partner (partner_id=10616) — campos RUC/sector ───────────
    print("\n\n══════════════════════════════════════════════════════════════════════")
    print("6. res.partner partner_id=10616 (CARRIER) — campos clave")
    print("══════════════════════════════════════════════════════════════════════")
    p2_rec = call("res.partner", "read", [[10616]],
                  {"fields": ["name","vat","street","city","state_id","country_id",
                               "sector","tex_city","function","l10n_latam_identification_type_id",
                               "contact_address","contact_address_complete"]})[0]
    for k,v in sorted(p2_rec.items()):
        if v is not False and v is not None and v != "":
            print(f"  {k:<45}: {repr(v)[:100]}")

    # ── 7. Muestra de 5 guías recientes COMPLETAS ────────────────────────────
    print("\n\n══════════════════════════════════════════════════════════════════════")
    print("7. MUESTRA 5 GUÍAS RECIENTES — campos encabezado + líneas")
    print("══════════════════════════════════════════════════════════════════════")
    guide_fields = ["id","name","l10n_latam_document_number","l10n_ec_authorization_number",
                    "l10n_ec_authorization_date","date","date_start","date_end",
                    "warehouse_id","client_id","partner_id","license_plate",
                    "animal_qty_total","state","line_ids","other_inf_ids","address_from","observation"]
    guides5 = call("account.remission.guide", "search_read",
                   [[("state","=","posted")]],
                   {"fields": guide_fields, "limit": 5, "order": "id desc"})
    for g in guides5:
        print(f"\n  Guía id={g['id']} | {g.get('name')} | state={g.get('state')}")
        for k,v in g.items():
            if v is not False and v is not None and v != "" and v != []:
                print(f"    {k}: {repr(v)[:90]}")
        # Fetch líneas
        if g.get("line_ids"):
            lines = call("account.remission.guide.line", "search_read",
                         [[("guide_id","=",g["id"])]],
                         {"fields": list(lf.keys())})
            for li in lines:
                print(f"    LINE id={li['id']}:")
                for k,v in li.items():
                    if v is not False and v is not None and v != "" and v != []:
                        print(f"      {k}: {repr(v)[:80]}")

    print("\n\n✅ Diagnóstico 2 completado")

if __name__ == "__main__":
    main()
