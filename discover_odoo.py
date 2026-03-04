import os, json, urllib.request, time

ODOO_URL = os.environ["ODOO_URL"]
ODOO_DB = os.environ["ODOO_DB"]
ODOO_USER = os.environ["ODOO_USER"]
ODOO_PASSWORD = os.environ["ODOO_PASSWORD"]
SESSION_COOKIE = None

def rpc(endpoint, params):
    global SESSION_COOKIE
    url = f"{ODOO_URL}{endpoint}"
    data = json.dumps({"jsonrpc":"2.0","method":"call","id":1,"params":params}).encode()
    headers = {"Content-Type": "application/json"}
    if SESSION_COOKIE:
        headers["Cookie"] = SESSION_COOKIE
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        if not SESSION_COOKIE:
            raw = r.headers.get("Set-Cookie","")
            if raw:
                SESSION_COOKIE = raw.split(";")[0]
        resp = json.loads(r.read())
    if "error" in resp:
        raise RuntimeError(json.dumps(resp["error"])[:400])
    return resp["result"]

def auth():
    r = rpc("/web/session/authenticate", {"db":ODOO_DB,"login":ODOO_USER,"password":ODOO_PASSWORD})
    print(f"UID: {r.get('uid')}")

def get_fields(model):
    return rpc("/web/dataset/call_kw", {
        "model":model,"method":"fields_get","args":[],
        "kwargs":{"attributes":["string","type","relation"]}
    })

def search_read(model, domain, fields, limit=3):
    return rpc("/web/dataset/call_kw", {
        "model":model,"method":"search_read","args":[domain],
        "kwargs":{"fields":fields,"limit":limit}
    })

def main():
    auth()

    # ── 1. Campos de account.remission.guide.stock.line ───────────────────
    print("="*60)
    print("CAMPOS: account.remission.guide.stock.line")
    print("="*60)
    try:
        fields = get_fields("account.remission.guide.stock.line")
        for name, info in sorted(fields.items()):
            rel = f" -> {info.get('relation','')}" if info.get("relation") else ""
            print(f"  {name:<45} {info.get('type',''):<15} {info.get('string','')}{rel}")

        print("\nMUESTRA: 3 stock.lines")
        all_f = list(fields.keys())
        sample = search_read("account.remission.guide.stock.line", [], all_f, limit=3)
        for i, s in enumerate(sample, 1):
            print(f"--- stock.line #{i} ---")
            for k, v in s.items():
                if v not in (False, None, [], ""):
                    print(f"  {k:<45} = {str(v)[:80]}")
    except Exception as e:
        print(f"ERROR stock.line: {e}")

    # ── 2. Campos personalizados en account.remission.guide ───────────────
    print("\n" + "="*60)
    print("CAMPOS CUSTOM en account.remission.guide (gavetas, plus, etc)")
    print("="*60)
    try:
        guide_fields = get_fields("account.remission.guide")
        keywords = ["gaveta","plus","salinidad","temperatura","temp",
                    "sal","caja","pallet","nauplio","larva","peso"]
        for name, info in sorted(guide_fields.items()):
            label = info.get("string","").lower()
            if any(k in name.lower() or k in label for k in keywords):
                print(f"  {name:<45} {info.get('type',''):<15} {info.get('string','')}")

        # Mostrar UNA guia con todos sus campos no vacios
        print("\nMUESTRA: guia ID=1 campos no vacios")
        all_f = list(guide_fields.keys())
        sample = search_read("account.remission.guide",
                             [["state","=","posted"]], all_f, limit=1)
        if sample:
            for k, v in sorted(sample[0].items()):
                if v not in (False, None, [], "", 0):
                    print(f"  {k:<45} = {str(v)[:80]}")
    except Exception as e:
        print(f"ERROR guide fields: {e}")

    # ── 3. Campos de remission.information.sri (other_inf_ids) ───────────
    print("\n" + "="*60)
    print("CAMPOS: remission.information.sri")
    print("="*60)
    try:
        fields = get_fields("remission.information.sri")
        for name, info in sorted(fields.items()):
            print(f"  {name:<45} {info.get('type',''):<15} {info.get('string','')}")
        sample = search_read("remission.information.sri", [], list(fields.keys()), limit=3)
        for i, s in enumerate(sample, 1):
            print(f"--- sri #{i} ---")
            for k, v in s.items():
                if v not in (False, None, [], ""):
                    print(f"  {k:<45} = {str(v)[:80]}")
    except Exception as e:
        print(f"ERROR sri: {e}")

    print("\nExploracion completa.")

main()
