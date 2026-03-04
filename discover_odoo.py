import os
import json
import urllib.request
import urllib.error

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
            raw_cookie = r.headers.get("Set-Cookie", "")
            if raw_cookie:
                SESSION_COOKIE = raw_cookie.split(";")[0]
        resp = json.loads(r.read())
    if "error" in resp:
        raise RuntimeError(json.dumps(resp["error"], indent=2))
    return resp["result"]


def authenticate():
    result = rpc("/web/session/authenticate", {
        "db": ODOO_DB,
        "login": ODOO_USER,
        "password": ODOO_PASSWORD
    })
    uid = result.get("uid")
    if not uid:
        raise RuntimeError(f"Autenticacion fallida: {result}")
    print(f"Autenticado correctamente - UID: {uid}")
    print(f"Usuario: {result.get('name')}")
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
