"""
Posts the newest generated post(s) to Instagram through the official
Instagram API with Instagram Login (Business or Creator account, no Facebook Page needed).
The 60-day login token is renewed automatically about every 3 weeks and stored
encrypted in secure/ig_token.enc (the key is derived from the IG_ACCESS_TOKEN secret).

- Feed post: the 4:5 version + full caption with 5 hashtags
- Story: the full 9:16 version (turn off with POST_STORY=0)
- Facebook Page: same 4:5 image + caption (turn off with POST_FACEBOOK=0)

Environment:
  IG_ACCESS_TOKEN               required (if missing, posting is skipped)
  IG_USER_ID                    optional (default "me")
  POST_NOW=1                    post even for manual runs
  POST_STORY                    "1" (default) or "0"
  GRAPH_VERSION                 default v23.0
"""

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
POSTS_JSON = ROOT / "docs" / "posts.json"
LAST_RUN = ROOT / ".last_run.json"
TZ = ZoneInfo("America/Los_Angeles")
SLOT_HOURS = {"morning": 9, "evening": 18}

SECRET_TOKEN = os.getenv("IG_ACCESS_TOKEN", "").strip()
# Instagram-login tokens start with "IG" and use graph.instagram.com (60 days, auto-renewed).
# Facebook / system-user tokens (never expire) use graph.facebook.com with the IG business account ID.
IG_LOGIN = SECRET_TOKEN.startswith("IG")
GRAPH_HOST = "https://graph.instagram.com" if IG_LOGIN else "https://graph.facebook.com"
GRAPH = f"{GRAPH_HOST}/{os.getenv('GRAPH_VERSION', 'v23.0')}"
# Instagram-login tokens always post as "me"; Facebook tokens need the IG business account ID.
IG_USER = "me" if IG_LOGIN else os.getenv("IG_USER_ID", "").strip()
TOKEN_FILE = ROOT / "secure" / "ig_token.enc"
TOKEN = SECRET_TOKEN


# ---------------------------------------------------------------- token storage
def _fernet():
    import base64, hashlib
    from cryptography.fernet import Fernet
    key = hashlib.sha256(("vns-ig-token|" + SECRET_TOKEN).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def load_token():
    """Use the renewed token if we have one that matches the current secret."""
    global TOKEN
    if TOKEN_FILE.exists():
        try:
            data = json.loads(_fernet().decrypt(TOKEN_FILE.read_bytes()))
            TOKEN = data["token"]
            return data.get("saved", "")
        except Exception:  # noqa: BLE001  (secret was replaced -> start fresh from it)
            print("Stored Instagram token doesn't match the current secret - using the secret.")
    return ""


def save_token(token):
    TOKEN_FILE.parent.mkdir(exist_ok=True)
    payload = json.dumps({"token": token, "saved": dt.datetime.now(TZ).isoformat(timespec="seconds")})
    TOKEN_FILE.write_bytes(_fernet().encrypt(payload.encode()))


def maybe_refresh(saved):
    """Renew the 60-day token if it's older than ~3 weeks (or age unknown)."""
    global TOKEN
    try:
        if saved and (dt.datetime.now(TZ) - dt.datetime.fromisoformat(saved)).days < 20:
            return
        url = f"{GRAPH_HOST}/refresh_access_token?" + urllib.parse.urlencode(
            {"grant_type": "ig_refresh_token", "access_token": TOKEN})
        with urllib.request.urlopen(url, timeout=60) as r:
            new = json.loads(r.read()).get("access_token")
        if new:
            TOKEN = new
            save_token(new)
            print("Instagram login renewed for another 60 days.")
    except urllib.error.HTTPError as e:
        # a brand-new token can't be renewed in its first 24 hours - that's fine
        print(f"Token renewal skipped (HTTP {e.code}); will try again next run.")
    except Exception as e:  # noqa: BLE001
        print(f"Token renewal skipped ({type(e).__name__}); will try again next run.")


def api(method, path, params):
    params = dict(params, access_token=TOKEN)
    data = urllib.parse.urlencode(params).encode()
    url = f"{GRAPH}/{path}"
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode())
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        for t in (TOKEN, SECRET_TOKEN):
            body = body.replace(t, "***") if t else body
        raise RuntimeError(f"Instagram API error {e.code}: {body[:400]}") from None


def _use_host(host):
    global GRAPH_HOST, GRAPH
    GRAPH_HOST = host
    GRAPH = f"{host}/{os.getenv('GRAPH_VERSION', 'v23.0')}"


def check_account():
    """Work out which Instagram server + account ID this token works with."""
    fb, ig = "https://graph.facebook.com", "https://graph.instagram.com"
    attempts = []
    if IG_USER:
        attempts.append((fb, IG_USER, {"fields": "username"}))
    attempts.append((ig, "me", {"fields": "user_id,username"}))
    attempts.append((fb, "me/accounts", None))
    for host, obj, params in attempts:
        _use_host(host)
        try:
            if params is None:
                return find_ig_account()
            info = api("GET", obj, params)
            print(f"Posting as @{info.get('username')} via {host.split('//')[1]}")
            return obj
        except Exception as e:  # noqa: BLE001
            print(f"  {host.split('//')[1]} / {obj}: no ({str(e)[:110]})")
    raise RuntimeError("This token can't reach the Instagram account - it may be missing publish permission.")


def find_ig_account():
    """For Facebook/system-user tokens: find the Instagram business account automatically."""
    data = api("GET", "me/accounts", {"fields": "name,instagram_business_account{id,username}"})
    for page in data.get("data", []):
        ig = page.get("instagram_business_account")
        if ig:
            print(f"Using Instagram account @{ig.get('username', '?')} (via page {page.get('name')})")
            return ig["id"]
    raise RuntimeError("No Instagram business account found for this token - add IG_USER_ID secret.")


def wait_ready(container_id):
    for _ in range(30):
        st = api("GET", container_id, {"fields": "status_code"}).get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram could not process the image (status {st})")
        time.sleep(5)
    raise RuntimeError("Instagram took too long to process the image")


def publish(image_url, caption=None, story=False):
    params = {"image_url": image_url}
    if story:
        params["media_type"] = "STORIES"
    else:
        params["caption"] = caption
    container = api("POST", f"{IG_USER}/media", params)["id"]
    wait_ready(container)
    media_id = api("POST", f"{IG_USER}/media_publish", {"creation_id": container})["id"]
    return media_id


def public_url(rel_path):
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.getenv("GITHUB_REF_NAME", "main")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/docs/{rel_path}"


def wait_for_slot(slot):
    if slot not in SLOT_HOURS:
        return
    now = dt.datetime.now(TZ)
    target = now.replace(hour=SLOT_HOURS[slot], minute=0, second=0, microsecond=0)
    secs = (target - now).total_seconds()
    if 0 < secs < 2 * 3600:
        print(f"Waiting {int(secs // 60)} min until {target.strftime('%I:%M %p')} Pacific to post...")
        time.sleep(secs)


# ---------------------------------------------------------------- Facebook Page
FB_GRAPH = f"https://graph.facebook.com/{os.getenv('GRAPH_VERSION', 'v23.0')}"
_PAGE = None


def fb_call(method, path, params, token):
    data = urllib.parse.urlencode(dict(params, access_token=token)).encode()
    url = f"{FB_GRAPH}/{path}"
    req = urllib.request.Request(url + "?" + data.decode()) if method == "GET" else \
        urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        for t in (token, TOKEN, SECRET_TOKEN):
            body = body.replace(t, "***") if t else body
        raise RuntimeError(f"Facebook API error {e.code}: {body[:400]}") from None


def facebook_page():
    """Find the salon's Facebook Page (the one linked to the Instagram account) and its posting token."""
    global _PAGE
    if _PAGE is None:
        _PAGE = False
        try:
            pages = fb_call("GET", "me/accounts",
                            {"fields": "id,name,access_token,instagram_business_account"}, SECRET_TOKEN).get("data", [])
        except Exception as e:  # noqa: BLE001
            print(f"Facebook Page lookup failed: {str(e)[:150]}")
            pages = []
        want = os.getenv("FB_PAGE_ID", "").strip()
        exact = [pg for pg in pages if want and pg.get("id") == want]
        linked = [pg for pg in pages if (pg.get("instagram_business_account") or {}).get("id") == IG_USER]
        pick = (exact or linked or pages or [None])[0]
        if pick and pick.get("access_token"):
            _PAGE = pick
            print(f"Facebook Page: {pick.get('name')}")
        else:
            print("Facebook Page not connected yet (token needs pages_manage_posts) - skipping Facebook.")
    return _PAGE or None


def post_to_facebook(p):
    page = facebook_page()
    if not page:
        return None
    r = fb_call("POST", f"{page['id']}/photos",
                {"url": public_url(p["feed_image"]), "caption": p["full_caption"], "published": "true"},
                page["access_token"])
    return {"post_id": r.get("post_id") or r.get("id"),
            "posted": dt.datetime.now(TZ).isoformat(timespec="seconds")}


def facebook_only():
    """[fb-test]: share the newest Instagram post to Facebook (if it isn't there yet)."""
    global IG_USER
    IG_USER = os.getenv("IG_USER_ID", "").strip()
    posts = json.loads(POSTS_JSON.read_text())
    todo = [p for p in posts if p.get("instagram") and not p["instagram"].get("error")
            and not (p.get("facebook") and not p["facebook"].get("error"))]
    if not todo:
        print("Nothing to share to Facebook.")
        return
    p = todo[-1]
    try:
        res = post_to_facebook(p)
        if res:
            p["facebook"] = res
            print(f"Shared {p['id']} to Facebook: {res}")
    except Exception as e:  # noqa: BLE001
        p["facebook"] = {"error": str(e)[:300]}
        print(f"FAILED Facebook share: {e}")
        POSTS_JSON.write_text(json.dumps(posts, indent=2, ensure_ascii=False))
        sys.exit(1)
    POSTS_JSON.write_text(json.dumps(posts, indent=2, ensure_ascii=False))


def main():
    if os.getenv("FB_TEST") == "1":
        return facebook_only()
    if LAST_RUN.exists():
        run = json.loads(LAST_RUN.read_text())
    elif os.getenv("POST_LATEST") == "1":
        latest = [p for p in json.loads(POSTS_JSON.read_text())
                  if not (p.get("instagram") and not p["instagram"].get("error"))]
        if not latest:
            print("Nothing waiting to be posted.")
            return
        run = {"ids": [latest[-1]["id"]], "slot": None}
    else:
        print("No new post this run.")
        return
    slot = run.get("slot")
    if not slot and os.getenv("POST_NOW") != "1":
        print("Manual run without 'post to Instagram' - skipping Instagram.")
        return
    if not SECRET_TOKEN:
        print("Instagram is not connected yet (IG_ACCESS_TOKEN missing) - skipping.")
        return
    global IG_USER
    print("Token type:", "Instagram login" if IG_LOGIN else "Facebook / system user")
    if IG_LOGIN:
        maybe_refresh(load_token())
    IG_USER = check_account()

    wait_for_slot(slot)
    posts = json.loads(POSTS_JSON.read_text())
    failed = False
    for p in posts:
        if p["id"] not in run["ids"] or (p.get("instagram") and not p["instagram"].get("error")):
            continue
        try:
            feed_id = publish(public_url(p["feed_image"]), caption=p["full_caption"])
            result = {"feed_id": feed_id, "posted": dt.datetime.now(TZ).isoformat(timespec="seconds")}
            if os.getenv("POST_STORY", "1") != "0":
                try:
                    result["story_id"] = publish(public_url(p["image"]), story=True)
                except Exception as e:  # noqa: BLE001
                    result["story_error"] = str(e)[:300]
            p["instagram"] = result
            print(f"Posted {p['id']} to Instagram: {result}")
            if os.getenv("POST_FACEBOOK", "1") != "0":
                try:
                    fb = post_to_facebook(p)
                    if fb:
                        p["facebook"] = fb
                        print(f"Shared {p['id']} to Facebook: {fb}")
                except Exception as e:  # noqa: BLE001
                    p["facebook"] = {"error": str(e)[:300]}
                    print(f"FAILED Facebook share for {p['id']}: {e}")
                    failed = True
        except Exception as e:  # noqa: BLE001
            p["instagram"] = {"error": str(e)[:300]}
            print(f"FAILED to post {p['id']}: {e}")
            failed = True
    POSTS_JSON.write_text(json.dumps(posts, indent=2, ensure_ascii=False))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
