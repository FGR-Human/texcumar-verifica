#!/usr/bin/env python3
"""
odoo_diagnostico.py — TEXCUMAR
Vuelca TODOS los campos del modelo account.remission.guide para el primer registro.
Ejecutar UNA VEZ para mapear los nombres reales de campos en Odoo.

Salida: diagnostico_output.txt
"""

import os, json, xmlrpc.client, sys
from datetime import datetime

ODOO_URL      = os.environ["ODOO_URL"].rstrip("/")
ODOO_DB       = os.environ["ODOO_DB"]
ODOO_USER     = os.environ["ODOO_USER"]
ODOO_PASSWORD = os.environ["ODOO_PASSWORD"]

def main():
    out = []
    def p(s=""):
        print(s)
        out.append(s)

    p("=" * 70)
    p(f"DIAGNÓSTICO ODOO — {datetime.now()}")
    p("=" * 70)

    common  = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    uid     = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
    if not uid:
        p("❌ Autenticación fallida")
        sys.exit(1)
    p(f"✅ UID: {uid}")

    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    def call(model, method, args, kw=None):
        return models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
                                 model, method, args, kw or {})

    MODEL = "account.remission.guide"

    # ── 1. Todos los campos del modelo ──────────────────────────────────────
    p(f"\n{'─'*70}")
    p(f"MODELO: {MODEL}")
    p(f"{'─'*70}")
    fields_meta = call(MODEL, "fields_get", [], {"attributes": ["string","type","relation"]})
    p(f"Total campos: {len(fields_meta)}\n")

    p(f"{'Campo':<45} {'Tipo':<15} {'Relación':<30} Etiqueta")
    p("-" * 110)
    for fname in sorted(fields_meta.keys()):
        fm = fields_meta[fname]
        rel = fm.get("relation", "")
        p(f"  {fname:<45} {fm['type']:<15} {rel:<30} {fm.get('string','')}")

    # ── 2. Primer registro con TODOS los campos simples ──────────────────────
    p(f"\n{'─'*70}")
    p("PRIMER REGISTRO — TODOS LOS CAMPOS")
    p(f"{'─'*70}")

    simple_types = {"char","text","integer","float","boolean","date",
                    "datetime","selection","monetary","many2one","html"}
    simple_fields = [f for f,m in fields_meta.items() if m["type"] in simple_types]

    records = call(MODEL, "search_read", [[]], {"fields": simple_fields, "limit": 1, "order": "id asc"})
    if not records:
        p("⚠️  Sin registros en el modelo")
        sys.exit(0)

    rec = records[0]
    p(f"ID: {rec.get('id')}\n")
    p(f"{'Campo':<45} {'Tipo':<15} Valor")
    p("-" * 100)
    for fname in sorted(rec.keys()):
        val   = rec[fname]
        ftype = fields_meta.get(fname, {}).get("type", "?")
        label = fields_meta.get(fname, {}).get("string", "")
        # Skip empty
        if val is False or val is None or val == "" or val == []:
            continue
        p(f"  {fname:<45} {ftype:<15} {repr(val)[:80]}")

    p(f"\n{'─'*70}")
    p("CAMPOS VACÍOS (False/None) en ese registro:")
    p(f"{'─'*70}")
    for fname in sorted(rec.keys()):
        val = rec[fname]
        if val is False or val is None or val == "" or val == []:
            ftype = fields_meta.get(fname, {}).get("type", "?")
            p(f"  {fname:<45} {ftype}")

    # ── 3. Campos relacionados al partner ────────────────────────────────────
    p(f"\n{'─'*70}")
    p("CAMPOS many2one RELACIONADOS (partner, carrier, etc.)")
    p(f"{'─'*70}")
    for fname, fm in sorted(fields_meta.items()):
        if fm["type"] == "many2one":
            val = rec.get(fname)
            p(f"  {fname:<45} → {fm.get('relation','?'):<30} valor={repr(val)}")

    # ── 4. One2many / Many2many (listas relacionadas) ────────────────────────
    p(f"\n{'─'*70}")
    p("CAMPOS one2many / many2many (líneas de producto, etc.)")
    p(f"{'─'*70}")
    for fname, fm in sorted(fields_meta.items()):
        if fm["type"] in ("one2many", "many2many"):
            val = rec.get(fname, [])
            p(f"  {fname:<45} → {fm.get('relation','?'):<35} (necesita fetch aparte)")

    # ── 5. Intentar fetch de líneas de producto ──────────────────────────────
    p(f"\n{'─'*70}")
    p("LÍNEAS DE PRODUCTO — buscar modelo relacionado")
    p(f"{'─'*70}")

    rec_id = rec["id"]
    line_models_to_try = [
        "l10n_ec.remission.guide.line",
        "account.remission.guide.line",
        "stock.move",
        "stock.move.line",
    ]
    for lm in line_models_to_try:
        try:
            lm_fields = call(lm, "fields_get", [], {"attributes": ["string","type","relation"]})
            # Find the field that links back to the guide
            link_field = None
            for fn, fm in lm_fields.items():
                if fm.get("relation") == MODEL:
                    link_field = fn
                    break
            if link_field:
                p(f"\n  ✅ Modelo '{lm}' tiene campo '{link_field}' → {MODEL}")
                lines = call(lm, "search_read",
                             [[(link_field, "=", rec_id)]],
                             {"limit": 3})
                p(f"     Líneas encontradas para rec_id={rec_id}: {len(lines)}")
                if lines:
                    p(f"     Primera línea:")
                    for k,v in lines[0].items():
                        if v is not False and v is not None and v != []:
                            p(f"       {k}: {repr(v)[:80]}")
                else:
                    p(f"     Campos disponibles en {lm}:")
                    for fn in sorted(lm_fields.keys())[:30]:
                        p(f"       {fn} ({lm_fields[fn]['type']})")
            else:
                p(f"\n  ℹ️  Modelo '{lm}' existe ({len(lm_fields)} campos) pero sin link directo a {MODEL}")
        except Exception as e:
            p(f"\n  ❌ Modelo '{lm}': {e}")

    # ── 6. Campos del res.partner del destinatario ───────────────────────────
    p(f"\n{'─'*70}")
    p("CAMPOS DEL PARTNER (res.partner) — para RUC, dirección, etc.")
    p(f"{'─'*70}")
    # Find partner field
    partner_val = None
    for fn in ["partner_id", "dest_partner_id", "commercial_partner_id"]:
        if fn in rec and rec[fn] is not False:
            partner_val = rec[fn]
            p(f"  Campo usado: {fn} = {repr(partner_val)}")
            break

    if partner_val:
        pid = partner_val[0] if isinstance(partner_val, list) else partner_val
        partner_fields = call("res.partner", "fields_get", [],
                              {"attributes": ["string","type"]})
        p(f"\n  Total campos res.partner: {len(partner_fields)}")
        partner_rec = call("res.partner", "read", [[pid]],
                           {"fields": list(partner_fields.keys())})[0]
        p(f"\n  Campos NO vacíos del partner {pid}:")
        for fn in sorted(partner_rec.keys()):
            v = partner_rec[fn]
            if v is not False and v is not None and v != "" and v != []:
                ftype = partner_fields.get(fn, {}).get("type","?")
                p(f"    {fn:<40} {ftype:<15} {repr(v)[:70]}")

    # ── Guardar output ──────────────────────────────────────────────────────
    output_text = "\n".join(out)
    with open("diagnostico_output.txt", "w") as f:
        f.write(output_text)
    print(f"\n✅ Diagnóstico guardado en diagnostico_output.txt")

if __name__ == "__main__":
    main()
