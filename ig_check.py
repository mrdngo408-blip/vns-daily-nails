"""Read-only Instagram connection check. Prints what the token can see. Never prints the token."""
import json, os, urllib.error, urllib.parse, urllib.request

TOKEN = os.getenv("IG_ACCESS_TOKEN", "").strip()
IG_ID = os.getenv("IG_USER_ID", "17841465658821849").strip()


def get(host, ver, path, params=None):
    q = dict(params or {}, access_token=TOKEN)
    url = f"{host}/{ver + '/' if ver else ''}{path}?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return "OK", json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read()).get("error", {})
            return "ERR", {k: err.get(k) for k in ("message", "type", "code", "error_subcode")}
        except Exception:  # noqa: BLE001
            return "ERR", {"http": e.code}


print("Token length:", len(TOKEN), "| starts with:", TOKEN[:3] if TOKEN else "(none)")
fb = "https://graph.facebook.com"
for ver in ("v26.0", "v23.0"):
    print(f"\n== graph.facebook.com {ver} ==")
    for path, params in [("me", {"fields": "id,name"}),
                         ("me/permissions", None),
                         (IG_ID, {"fields": "id,username"}),
                         ("me/accounts", {"fields": "name,instagram_business_account{id,username}"})]:
        st, data = get(fb, ver, path, params)
        print(f"{path}: {st} {json.dumps(data)[:400]}")
