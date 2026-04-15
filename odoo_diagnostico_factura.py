#!/usr/bin/env python3
"""
odoo_diagnostico_factura.py v2 — TEXCUMAR
Encuentra el número de factura ligado a cada guía de remisión.
Versión robusta: verifica qué campos existen antes de consultarlos.
"""

import os, sys, xmlrpc.client
from datetime import datetime

ODOO_URL      = os.environ["ODOO_URL"].rstrip("/")
ODOO_DB       = os.environ["ODOO_DB"]
ODOO_USER     = os.environ["ODOO_USER"]
ODOO_PASSWORD = os.environ["ODOO_PASSWORD"]

def connect():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
    uid    = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
    if not uid:
        sys.exit("❌ Autenticación fallida")
    print(f"✅ Conectado — UID {uid}")
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
    return uid, models

def rpc(models, uid, model, method, args, kw=None):
    return models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, model, method, args, kw or {})

def main():
    print("=" * 70)
    print(f"DIAGNÓSTICO FACTURA — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    uid, models = connect()

    # ── PASO 1: Campos reales del modelo ─────────────────────────────────────
    print("\n📋 Campos reales de account.remission.guide...")
    all_fields = rpc(models, uid, "account.remission.guide", "fields_get", [],
                     {"attributes": ["string", "type", "relation"]})
    print(f"   Total campos: {len(all_fields)}")

    keywords = ["invoice", "move", "sale", "picking", "origin", "account",
                "factura", "pedido", "order", "bill"]
    candidatos = {}
    print("\n🔍 Campos candidatos:")
    for fname, fmeta in sorted(all_fields.items()):
        if any(kw in fname.lower() for kw in keywords):
            candidatos[fname] = fmeta
            rel = fmeta.get("relation", "")
            print(f"   {fname:<45} {fmeta['type']:<15} {rel:<30} {fmeta.get('string','')}")

    if not candidatos:
        print("   (ninguno encontrado)")

    # ── PASO 2: Cargar guías solo con campos que existen ─────────────────────
    safe_fields = ["id", "name", "l10n_latam_document_number", "state", "line_ids"]
    safe_fields += [f for f in candidatos]

    print(f"\n📥 Cargando 3 guías recientes...")
    guias = rpc(models, uid, "account.remission.guide", "search_read",
                [[["state", "=", "posted"]]],
                {"fields": safe_fields, "limit": 3, "order": "id desc"})

    if not guias:
        sys.exit("❌ Sin guías en estado posted")
    print(f"   {len(guias)} guías cargadas")

    # ── PASO 3: Valores por guía ──────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("VALORES DE CAMPOS CANDIDATOS POR GUÍA")
    print("=" * 70)
    for guia in guias:
        gnum = guia.get("l10n_latam_document_number") or guia.get("name")
        print(f"\n📄 Guía: {gnum} (id={guia['id']})")
        for fname in sorted(candidatos.keys()):
            val = guia.get(fname)
            if val and val is not False and val != []:
                print(f"   ✅ {fname}: {repr(val)[:100]}")
            else:
                print(f"   ⬜ {fname}: vacío")

    # ── PASO 4: Líneas de guía ────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("CAMPOS DE account.remission.guide.line")
    print("="*70)
    try:
        line_fields = rpc(models, uid, "account.remission.guide.line", "fields_get", [],
                          {"attributes": ["string", "type", "relation"]})
        print(f"   Total campos: {len(line_fields)}")
        for fname, fmeta in sorted(line_fields.items()):
            if any(kw in fname.lower() for kw in keywords):
                rel = fmeta.get("relation", "")
                print(f"   {fname:<45} {fmeta['type']:<15} {rel:<30} {fmeta.get('string','')}")

        # Leer líneas de la primera guía
        first_id = guias[0]["id"]
        simple = [f for f, m in line_fields.items()
                  if m["type"] not in ("one2many", "many2many", "binary", "html")]
        lines = rpc(models, uid, "account.remission.guide.line", "search_read",
                    [[["guide_id", "=", first_id]]],
                    {"fields": simple, "limit": 2})
        if lines:
            print(f"\n   Campos con valor en línea (guía id={first_id}):")
            for k, v in sorted(lines[0].items()):
                if v and v is not False and v != []:
                    if any(kw in k.lower() for kw in keywords):
                        print(f"   ✅ {k}: {repr(v)[:100]}")
    except Exception as e:
        print(f"   ⚠️  Error: {e}")

    # ── PASO 5: account.move → campos que apuntan a la guía ──────────────────
    print(f"\n{'='*70}")
    print("CAMPOS EN account.move QUE APUNTAN A LA GUÍA")
    print("="*70)
    try:
        move_fields = rpc(models, uid, "account.move", "fields_get", [],
                          {"attributes": ["string", "type", "relation"]})
        guide_refs = [(f, m) for f, m in move_fields.items()
                      if m.get("relation") == "account.remission.guide"
                      or "remission" in f.lower() or "guide" in f.lower()]
        if guide_refs:
            print("   ✅ Campos encontrados:")
            for fname, fmeta in guide_refs:
                print(f"   {fname:<45} {fmeta['type']:<15} {fmeta.get('string','')}")
        else:
            print("   ⬜ No hay campos directos hacia la guía en account.move")

        print("\n   Campos de account.move relacionados con origen/picking/sale:")
        for fname, fmeta in sorted(move_fields.items()):
            if any(kw in fname.lower() for kw in ["origin", "picking", "sale", "source"]):
                print(f"   {fname:<45} {fmeta['type']:<15} {fmeta.get('string','')}")
    except Exception as e:
        print(f"   ⚠️  Error: {e}")

    # ── PASO 6: Búsqueda directa por origin ──────────────────────────────────
    print(f"\n{'='*70}")
    print("BÚSQUEDA DIRECTA EN account.move POR ORIGIN")
    print("="*70)
    try:
        guia     = guias[0]
        gnum     = guia.get("l10n_latam_document_number") or ""
        gname    = guia.get("name") or ""
        for search_val in list(set([gname, gnum])):
            if not search_val:
                continue
            invoices = rpc(models, uid, "account.move", "search_read",
                           [[["invoice_origin", "like", search_val],
                             ["move_type", "in", ["out_invoice", "out_refund"]]]],
                           {"fields": ["name", "l10n_latam_document_number",
                                       "move_type", "state", "invoice_origin"],
                            "limit": 3})
            if invoices:
                print(f"\n✅ FACTURAS encontradas con origin='{search_val}':")
                for inv in invoices:
                    num = inv.get("l10n_latam_document_number") or inv.get("name")
                    print(f"   Factura: {num} | origen: {inv.get('invoice_origin')} | estado: {inv.get('state')}")
            else:
                print(f"⬜ Sin facturas con origin='{search_val}'")
    except Exception as e:
        print(f"   ⚠️  Error: {e}")

    print(f"\n{'='*70}")
    print("FIN DEL DIAGNÓSTICO — comparte este log completo")
    print("="*70)

if __name__ == "__main__":
    main()
