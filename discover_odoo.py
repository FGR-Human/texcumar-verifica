import os
import json
import urllib.request

ODOO_URL = os.environ["ODOO_URL"]
ODOO_DB = os.environ["ODOO_DB"]
ODOO_USER = os.environ["ODOO_USER"]
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
        "model": "res.users",
        "method": "authenticate",
        "args": [ODOO_DB, ODOO_USER, ODOO_PASSWORD, {}],
        "kwargs": {}
    })
    print(f"Autenticado UID: {uid}")
    return uid


def get_fields(model):
    return rpc("/web/dataset/call_kw", {
        "model": model,
        "method": "fields_get",
        "args": [],
        "kwargs": {"attributes": ["string", "type", "relation"]}
    })


def search_read(model, domain, fields, limit=3):
    return rpc("/web/dataset/call_kw", {
        "model": model,
        "method": "search_read",
        "args": [domain],
        "kwargs": {"fields": fields, "limit": limit}
    })


def main():
    authenticate()

    print("=" * 60)
    print("CAMPOS: account.remission.guide")
    print("=" * 60)
    fields = get_fields("account.remission.guide")
    for name, info in sorted(fields.items()):
        rel = f" -> {info.get('relation', '')}" if info.get("relation") else ""
        print(f"  {name:<45} {info.get('type', ''):<15} {info.get('string', '')}{rel}")

    print("=" * 60)
    print("MUESTRA: 3 guias posted")
    print("=" * 60)
    all_fields = list(fields.keys())
    sample = search_read(
        "account.remission.guide",
        [["state", "=", "posted"]],
        all_fields,
        limit=3
    )
    for i, guide in enumerate(sample, 1):
        print(f"--- Guia #{i} ---")
        for k, v in guide.items():
            if v not in (False, None, [], ""):
                print(f"  {k:<45} = {str(v)[:80]}")

    print("Exploracion completa.")


main()
