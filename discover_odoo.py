  """
discover_odoo.py — TEXCUMAR
Ejecutar UNA VEZ para mapear los campos reales de Odoo.
Resultado: imprime todos los campos disponibles y una muestra de datos.
"""
import os, json, urllib.request, urllib.parse

ODOO_URL      = os.environ["ODOO_URL"]
ODOO_DB       = os.environ["ODOO_DB"]
ODOO_USER     = os.environ["ODOO_USER"]
ODOO_PASSWORD = os.environ["ODOO_PASSWORD"]


def rpc(endpoint, params):
    url = f"{ODOO_URL}{endpoint}"
    data = json.dumps({"jsonrpc": "2.0", "method": "call", "id": 1, "params": params}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())
    if "error" in resp:
        raise RuntimeError(resp["error"])
    return resp["result"]


def authenticate():
    uid = rpc("/web/dataset/call_kw", {
        "model": "res.users", "method": "authenticate",
        "args": [ODOO_DB, ODOO_USER, ODOO_PASSWORD, {}], "kwargs": {}
    })
    print(f"✅ Autenticado — UID: {uid}")
    return uid


def get_fields(uid, model):
    result = rpc("/web/dataset/call_kw", {
        "model": model, "method": "fields_get",
        "args": [], "kwargs": {
            "attributes": ["string", "type", "relation"],
            "context": {"uid": uid}
        }
    })
    return result


def search_read(uid, model, domain, fields, limit=3):
    return rpc("/web/dataset/call_kw", {
        "model": model, "method": "search_read",
        "args": [domain], "kwargs": {
            "fields": fields,
            "limit": limit,
            "context": {"uid": uid}
        }
    })


def main():
    uid = authenticate()

    # ── 1. Campos del modelo principal ───────────────────────────────────
    print("\n" + "="*70)
    print("CAMPOS: account.remission.guide")
    print("="*70)
    fields = get_fields(uid, "account.remission.guide")
    for name, info in sorted(fields.items()):
        rel = f" → {info.get('relation','')}" if info.get('relation') else ""
        print(f"  {name:<45} {info.get('type',''):<15} {info.get('string','')}{rel}")

    # ── 2. Muestra de 3 guías publicadas ─────────────────────────────────
    print("\n" + "="*70)
    print("MUESTRA: 3 guías en estado 'posted'")
    print("="*70)
    all_field_names = list(fields.keys())
    sample = search_read(uid, "account.remission.guide",
                         [["state", "=", "posted"]], all_field_names, limit=3)
    for i, guide in enumerate(sample, 1):
        print(f"\n--- Guía #{i} ---")
        for k, v in guide.items():
            if v not in (False, None, [], ""):
                print(f"  {k:<45} = {str(v)[:80]}")

    # ── 3. Campos de stock.picking (líneas de entrega) ───────────────────
    print("\n" + "="*70)
    print("CAMPOS: stock.picking (entregas relacionadas)")
    print("="*70)
    picking_fields = get_fields(uid, "stock.picking")
    for name, info in sorted(picking_fields.items()):
        print(f"  {name:<45} {info.get('type',''):<15} {info.get('string','')}")

    # ── 4. Muestra de 1 picking relacionado ──────────────────────────────
    if sample and sample[0].get("picking_ids"):
        picking_id = sample[0]["picking_ids"][0]
        print(f"\n--- stock.picking #{picking_id} ---")
        picking_data = search_read(uid, "stock.picking",
                                   [["id", "=", picking_id]],
                                   list(picking_fields.keys()), limit=1)
        if picking_data:
            for k, v in picking_data[0].items():
                if v not in (False, None, [], ""):
                    print(f"  {k:<45} = {str(v)[:80]}")

    # ── 5. Campos de stock.move ───────────────────────────────────────────
    print("\n" + "="*70)
    print("CAMPOS: stock.move (líneas de producto)")
    print("="*70)
    move_fields = get_fields(uid, "stock.move")
    important = ["name", "product_id", "product_uom_qty", "quantity_done",
                 "product_uom", "state", "picking_id", "location_id",
                 "location_dest_id", "lot_ids"]
    for name in important:
        if name in move_fields:
            info = move_fields[name]
            print(f"  {name:<45} {info.get('type',''):<15} {info.get('string','')}")

    print("\n✅ Exploración completa. Revisa los campos arriba para confirmar el mapeo.")


if __name__ == "__main__":
    main()
