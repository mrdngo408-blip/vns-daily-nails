"""
Posts the newest generated post(s) to Instagram through the official
Instagram Graph API (Instagram Business account linked to a Facebook Page).

- Feed post: the 4:5 version + full caption with 5 hashtags
- Story: the full 9:16 version (turn off with POST_STORY=0)

Environment:
  IG_USER_ID, IG_ACCESS_TOKEN   required (if missing, posting is skipped)
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

GRAPH = f"https://graph.facebook.com/{os.getenv('GRAPH_VERSION', 'v23.0')}"
IG_USER = os.getenv("IG_USER_ID", "").strip()
TOKEN = os.getenv("IG_ACCESS_TOKEN", "").strip()


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
        body = body.replace(TOKEN, "***") if TOKEN else body
        raise RuntimeError(f"Instagram API error {e.code}: {body[:400]}") from None


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


def main():
    if not LAST_RUN.exists():
        print("No new post this run.")
        return
    run = json.loads(LAST_RUN.read_text())
    slot = run.get("slot")
    if not slot and os.getenv("POST_NOW") != "1":
        print("Manual run without 'post to Instagram' - skipping Instagram.")
        return
    if not (IG_USER and TOKEN):
        print("Instagram is not connected yet (IG_USER_ID / IG_ACCESS_TOKEN missing) - skipping.")
        return

    wait_for_slot(slot)
    posts = json.loads(POSTS_JSON.read_text())
    failed = False
    for p in posts:
        if p["id"] not in run["ids"] or p.get("instagram"):
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
        except Exception as e:  # noqa: BLE001
            p["instagram"] = {"error": str(e)[:300]}
            print(f"FAILED to post {p['id']}: {e}")
            failed = True
    POSTS_JSON.write_text(json.dumps(posts, indent=2, ensure_ascii=False))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
