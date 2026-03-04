import os
import json
import urllib.request

ODOO_URL = os.environ["ODOO_URL"]
ODOO_DB = os.environ["ODOO_DB"]
ODOO_USER = os.environ["ODOO_USER"]
ODOO_PASSWORD = os.environ["ODOO_PASSWORD"]

SESSION_COOKIE = None

def rpc(endpoint, params):
    global SESSION_COOKIE
    url = f"{ODOO_URL}{endpoint}"
    data = json.dumps({"jsonrpc": "2.0", "method": "call", "id": 1, "params": params}).encode()
    headers = {"Content-Type": "application/json"}
    if SESSION_COOKIE:
        headers["Cookie"] = SESSION_COOKIE
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        if not SESSION_COOKIE:
            raw = r.headers.get("Set-Cookie", "")
            if raw:
                SESSION_COOKIE = raw.split(";")[0]
        resp = json.loads(r.read())
    if "error" in resp:
        raise RuntimeError(json.dumps(resp["error"])[:300])
    return resp["result"]

def authenticate():
    result = rpc("/web/session/authenticate", {
        "db": ODOO_DB, "login": ODOO_USER, "password": ODOO_PASSWORD
    })
    print(f"Autenticado UID: {result.get('uid')}")
    return result.get("uid")

def get_fields(model):
    return rpc("/web/dataset/call_kw", {
        "model": model, "method": "fields_get",
        "args": [], "kwargs": {"attributes": ["string", "type", "relation"]}
    })

def search_read(model, domain, fields, limit=3):
    return rpc("/web/dataset/call_kw", {
        "model": model, "method": "search_read",
        "args": [domain], "kwargs": {"fields": fields, "limit": limit}
    })

def main():
    authenticate()

    # ── Campos de account.remission.guide.line ─────────────────────────────
    print("=" * 60)
    print("CAMPOS: account.remission.guide.line")
    print("=" * 60)
    try:
        fields = get_fields("account.remission.guide.line")
        for name, info in sorted(fields.items()):
            rel = f" -> {info.get('relation','')}" if info.get("relation") else ""
            print(f"  {name:<45} {info.get('type',''):<15} {info.get('string','')}{rel}")

        print("=" * 60)
        print("MUESTRA: 3 lineas reales")
        print("=" * 60)
        all_field_names = list(fields.keys())
        sample = search_read("account.remission.guide.line", [], all_field_names, limit=3)
        for i, line in enumerate(sample, 1):
            print(f"--- Linea #{i} ---")
            for k, v in line.items():
                if v not in (False, None, [], ""):
                    print(f"  {k:<45} = {str(v)[:80]}")
    except Exception as e:
        print(f"ERROR: {e}")

    # ── Verificar campos de guia relacionados a lineas ─────────────────────
    print("=" * 60)
    print("MUESTRA: 1 guia con sus line_ids")
    print("=" * 60)
    try:
        guias = search_read(
            "account.remission.guide",
            [["state","=","posted"]],
            ["name", "line_ids"],
            limit=1
        )
        if guias:
            g = guias[0]
            print(f"Guia: {g['name']}")
            print(f"line_ids: {g.get('line_ids')}")
            # Intentar leer esa linea especifica
            if g.get("line_ids"):
                lid = g["line_ids"][0]
                print(f"\nIntentando leer linea ID={lid} con todos los campos...")
                line_fields = get_fields("account.remission.guide.line")
                line = search_read(
                    "account.remission.guide.line",
                    [["id","=",lid]],
                    list(line_fields.keys()),
                    limit=1
                )
                if line:
                    for k, v in line[0].items():
                        if v not in (False, None, [], ""):
                            print(f"  {k:<45} = {str(v)[:80]}")
    except Exception as e:
        print(f"ERROR guia con lineas: {e}")

    print("Exploracion completa.")

main()
