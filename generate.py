"""
Venetian Nail Spa - Daily Luxury Nail Post Generator

Makes one 9:16 (1080x1920) luxury nail image + an Instagram caption with
exactly 5 hashtags, based on the season and upcoming holidays.
Results go to docs/posts/ and docs/posts.json (shown on the gallery page).

Environment variables:
  OPENAI_API_KEY   required (unless MOCK=1)
  MODE             "slot" (scheduled: 9 AM / 6 PM post), "one" (one post now),
                   or "samples" (one post per season)
  FORCE_SEASON     optional: winter / spring / summer / fall
  IMAGE_QUALITY    optional: high (default) / medium / low
  MOCK             "1" = no API calls, makes placeholder images (for testing)
"""

import base64
import datetime as dt
import hashlib
import io
import json
import os
import random
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
POSTS_DIR = DOCS / "posts"
POSTS_JSON = DOCS / "posts.json"
STATUS_JSON = DOCS / "status.json"
TZ = ZoneInfo("America/Los_Angeles")

IMAGE_MODELS = [m for m in os.getenv("IMAGE_MODELS", "gpt-image-2,gpt-image-1.5,gpt-image-1").split(",") if m]
TEXT_MODELS = [m for m in os.getenv("TEXT_MODELS", "gpt-5-mini,gpt-4.1-mini,gpt-4o-mini").split(",") if m]
IMAGE_QUALITY = os.getenv("IMAGE_QUALITY", "medium") or "medium"
IMAGE_SIZES = ["1088x1920", "1024x1536"]  # true 9:16 first, then the standard tall size
MOCK = os.getenv("MOCK") == "1"

BUSINESS = {
    "name": "Venetian Nail Spa",
    "city": "Modesto, CA",
    "phone": "209-408-8597",
    "booking": "https://book.gocheckin.net/v3/nails/venetian-nail-spa-17058/choose-service",
}

LOCAL_TAGS = ["#ModestoNails", "#VenetianNailSpaModesto", "#ModestoCA", "#CentralValleyNails"]

# ---------------------------------------------------------------- style
POSES_DIR = ROOT / "refs" / "poses"   # reference photos (NN-name.jpg) + descriptions (NN-name.txt)


def load_poses():
    poses = []
    for img in sorted(POSES_DIR.glob("*.jpg")) + sorted(POSES_DIR.glob("*.png")):
        txt = img.with_suffix(".txt")
        poses.append({"name": img.stem, "path": img,
                      "desc": txt.read_text().strip() if txt.exists() else "the same hand pose as the reference photo"})
    return poses


def pick_pose(posts):
    """Rotate poses: use the one used least recently (never the same pose twice in a row if there are 2+)."""
    poses = load_poses()
    if not poses:
        return None
    last_used = {}
    for i, p in enumerate(posts):
        if p.get("pose"):
            last_used[p["pose"]] = i
    return sorted(poses, key=lambda x: (last_used.get(x["name"], -1), random.random()))[0]


CURRENT_POSE = {"pose": None}

REFERENCE_STYLE = (
    "Ultra-realistic luxury beauty-editorial close-up photograph of ONE woman's hand. POSE (copy the reference "
    "photo exactly): {pose} "
    "{nails}"
)

LUXE_NAILS = ("NAILS: long {shape} nails, perfectly manicured, clean cuticles, flawless high-gloss gel finish, with "
              "{metal} chrome accents and small crystals where the design calls for them. All visible nails form "
              "one coordinated salon set.")
FRENCH_NAILS = ("NAILS: a CLASSIC FRENCH TIP set on {shape} nails: sheer natural pink/nude base on every nail with a "
                "crisp, clean, evenly curved tip line ({tip}). Add a small, delicate design accent on only one or two "
                "nails ({accent}); the rest stay clean classic French. Flawless high-gloss gel finish, clean cuticles. "
                "Elegant, timeless and understated - no heavy chrome, no big crystals, no busy patterns.")
FRENCH_TIPS = ["bright white tips", "soft milky white tips", "thin micro-French white tips", "deep French white tips",
               "seasonal colored tips in {color}", "double-line French with a thin {metal} line under white tips"]
FRENCH_ACCENTS = ["fine hand-painted line art", "tiny seasonal florals", "a few micro pearls", "a single small gem",
                  "a delicate {metal} foil fleck", "a tiny bow", "a minimalist leaf or vine", "a small heart",
                  "subtle dotted detail along the smile line", "a soft swirl"]
FRENCH_SHAPES = ["almond", "almond", "medium almond", "soft square", "oval", "short almond"]


def want_french(posts):
    """Classic French tip every 3-4 real posts."""
    since = 0
    for p in reversed([x for x in posts if not x.get("test")]):
        if p.get("style") == "french":
            break
        since += 1
    return since >= 4 or (since >= 3 and random.random() < 0.5)

REALISM_RULES = (
    "Anatomically perfect: exactly ONE hand and nothing else (no second hand, no extra fingers). Every "
    "visible finger has exactly three natural segments and natural knuckle creases, attached naturally to the "
    "hand, none fused, bent backwards, duplicated or missing; correct finger lengths (middle longest, pinky "
    "shortest); one nail per finger, every nail fully inside the frame. Realistic skin texture and fine lines, "
    "elegant slim fingers, {skin} skin tone. Nails razor sharp and in focus, background softly out of focus. "
    "Soft natural window light with gentle highlights on the gloss. Tall vertical portrait composition. "
    "No text, no letters, no logos, no watermarks, no brand names."
)

SHAPES = ["almond", "almond", "stiletto", "long oval", "almond", "soft stiletto"]
SKIN_TONES = ["fair", "light olive", "medium tan", "warm golden tan", "deep brown", "rich medium brown"]
METALS = ["gold", "gold", "rose gold", "champagne gold", "silver", "gold"]

TECHNIQUES = [
    "gold chrome stained-glass outlines", "cat-eye magnetic gel", "aura airbrush glow", "soft ombre",
    "marble with metallic veins", "modern French tip variation", "3D gel florals", "gold leaf flakes",
    "rhinestone clusters", "glazed pearl chrome", "abstract flowing swirls", "negative-space design",
    "jelly glass effect", "galaxy sparkle ombre", "velvet cat-eye shimmer",
]

SEASONS = {
    "winter": {
        "palettes": ["icy blue, silver and pearl white", "deep burgundy, champagne and gold",
                     "emerald, gold and milky white", "midnight navy, silver glitter and ice white",
                     "black cherry, rose gold and blush"],
        "accents": "snowflake and crystal accents, velvet and frost effects",
        "backgrounds": ["ivory chunky knit sweater", "charcoal cashmere sweater", "deep emerald velvet", "white faux fur throw"],
    },
    "spring": {
        "palettes": ["pastel lavender, blush pink and pearl", "mint, butter yellow and white",
                     "soft peach, lilac and gold", "baby blue, blush and pearl chrome",
                     "sage green, cream and rose gold"],
        "accents": "soft florals, pearls, delicate French tips with a twist",
        "backgrounds": ["cream satin", "blush pink knit sweater", "soft white linen", "lavender cashmere"],
    },
    "summer": {
        "palettes": ["coral, turquoise and gold", "hot pink, citrus orange and gold",
                     "ocean blue, aqua and pearl", "neon pink, lilac and chrome", "sunset orange, magenta and gold"],
        "accents": "tropical and ocean themes, chrome and aura effects, a touch of neon",
        "backgrounds": ["white linen shirt", "champagne silk", "cream satin", "light beige knit"],
    },
    "fall": {
        "palettes": ["burgundy, burnt orange and gold", "mocha, caramel and champagne",
                     "deep plum, olive and gold", "tortoiseshell, espresso and gold leaf",
                     "copper, wine red and cream"],
        "accents": "tortoiseshell, gold leaf, cat-eye shimmer, warm metallics",
        "backgrounds": ["chocolate brown ribbed knit sweater", "camel cashmere", "mocha velvet", "cream cable-knit sweater"],
    },
}


# ---------------------------------------------------------------- calendar
def season_for(d: dt.date) -> str:
    m = d.month
    if m in (12, 1, 2):
        return "winter"
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    return "fall"


def _easter(y: int) -> dt.date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(y, month, day)


def _nth_weekday(y, month, weekday, n):
    d = dt.date(y, month, 1)
    while d.weekday() != weekday:
        d += dt.timedelta(days=1)
    return d + dt.timedelta(weeks=n - 1)


def holidays_for(y: int):
    return {
        "New Year's Eve (black, gold and glitter)": dt.date(y, 12, 31),
        "Valentine's Day (reds, pinks, hearts)": dt.date(y, 2, 14),
        "St. Patrick's Day (emerald and gold)": dt.date(y, 3, 17),
        "Easter (soft pastels)": _easter(y),
        "Mother's Day (soft florals, blush and pearl)": _nth_weekday(y, 5, 6, 2),
        "4th of July (red, white and blue, elegant)": dt.date(y, 7, 4),
        "Halloween (elegant, moody, not cartoon)": dt.date(y, 10, 31),
        "Thanksgiving (warm harvest tones)": _nth_weekday(y, 11, 3, 4),
        "Christmas (red, emerald, gold, festive sparkle)": dt.date(y, 12, 25),
    }


def upcoming_holiday(d: dt.date):
    """Holiday within the next 10 days (Valentine's: whole Feb 1-14)."""
    for y in (d.year, d.year + 1):
        for name, hd in holidays_for(y).items():
            window = 13 if name.startswith("Valentine") else 10
            if 0 <= (hd - d).days <= window:
                return name
    return None


# ---------------------------------------------------------------- data
def load_posts():
    if POSTS_JSON.exists():
        return json.loads(POSTS_JSON.read_text())
    return []


def save_posts(posts):
    POSTS_JSON.write_text(json.dumps(posts, indent=2, ensure_ascii=False))


def write_status(ok: bool, message: str):
    STATUS_JSON.write_text(json.dumps({
        "ok": ok, "message": message,
        "time": dt.datetime.now(TZ).strftime("%Y-%m-%d %I:%M %p PT"),
    }, indent=2))


# ---------------------------------------------------------------- AI calls
def openai_client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


# ---------------------------------------------------------------- spending guard
USAGE_JSON = DOCS / "usage.json"
_QUALITY_COST = {"high": 0.30, "medium": 0.08, "low": 0.03}
_q = os.getenv("IMAGE_QUALITY", "medium") or "medium"               # cautious estimates per image
COST_PER_IMAGE = float(os.getenv("COST_PER_IMAGE", "") or _QUALITY_COST.get(_q, 0.30))
DAILY_IMAGE_LIMIT = int(os.getenv("DAILY_IMAGE_LIMIT", "8") or 8)         # 3 posts x 2 tries + 2 spare
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "10") or 10)                # ignore repeat manual runs
MONTHLY_BUDGET = float(os.getenv("MONTHLY_BUDGET_USD", "15") or 15)       # stop before going over this
MANUAL_DAILY_LIMIT = int(os.getenv("MANUAL_DAILY_LIMIT", "2") or 2)       # "Make one now" images per day


class BudgetStop(Exception):
    """Spending limit reached - stop before calling OpenAI."""


def load_usage():
    try:
        return json.loads(USAGE_JSON.read_text())
    except Exception:  # noqa: BLE001
        return {"days": {}, "months": {}}


def usage_now():
    u = load_usage()
    now = dt.datetime.now(TZ)
    d, m = now.date().isoformat(), now.strftime("%Y-%m")
    return u, u["days"].get(d, {"images": 0, "cost": 0.0}), u["months"].get(m, {"images": 0, "cost": 0.0}), d, m


TEST_DAILY_LIMIT = int(os.getenv("TEST_DAILY_LIMIT", "12") or 12)
TESTING = {"on": False}


def budget_check():
    _, day, month, d, m = usage_now()
    if TESTING["on"] and day.get("test_images", 0) >= TEST_DAILY_LIMIT:
        raise BudgetStop(f"Daily test limit reached ({day.get('test_images', 0)}/{TEST_DAILY_LIMIT} test images).")
    if not TESTING["on"] and day["images"] - day.get("test_images", 0) >= DAILY_IMAGE_LIMIT:
        raise BudgetStop(f"Daily safety limit reached ({day['images']}/{DAILY_IMAGE_LIMIT} images on {d}). "
                         f"No more images until tomorrow.")
    if month["cost"] + COST_PER_IMAGE > MONTHLY_BUDGET:
        raise BudgetStop(f"Monthly budget reached (about ${month['cost']:.2f} of ${MONTHLY_BUDGET:.0f} in {m}). "
                         f"No more images this month.")


def record_image(kind="image"):
    u, day, month, d, m = usage_now()
    for bucket in (day, month):
        bucket["images"] = bucket.get("images", 0) + 1
        if TESTING["on"]:
            bucket["test_images"] = bucket.get("test_images", 0) + 1
        bucket["cost"] = round(bucket.get("cost", 0.0) + COST_PER_IMAGE, 2)
    u["days"][d], u["months"][m] = day, month
    u["days"] = dict(sorted(u["days"].items())[-60:])          # keep 60 days
    u["limits"] = {"daily_images": DAILY_IMAGE_LIMIT, "monthly_budget": MONTHLY_BUDGET,
                   "cost_per_image_estimate": COST_PER_IMAGE}
    USAGE_JSON.write_text(json.dumps(u, indent=2))


class FatalAPIError(Exception):
    """Billing / key / quota / policy problems: retrying would only waste time or money."""


FATAL_HINTS = ("insufficient_quota", "billing", "hard_limit", "exceeded your current quota",
               "invalid_api_key", "incorrect api key", "401", "account_deactivated",
               "content_policy", "moderation_blocked", "safety system")


def is_fatal(e):
    if isinstance(e, (FatalAPIError, BudgetStop)):
        return True
    msg = f"{type(e).__name__} {e}".lower()
    return any(h in msg for h in FATAL_HINTS)


def with_retries(fn, label, tries=3):
    last = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if is_fatal(e):
                raise FatalAPIError(f"{label}: {str(e)[:300]}") from None
            last = e
            print(f"  {label} attempt {attempt} failed: {type(e).__name__}: {str(e)[:200]}")
            time.sleep(4 * attempt)
    raise last


def build_brief(day: dt.date, season: str, holiday, recent):
    s = SEASONS[season]
    rnd = random.Random(f"{day.isoformat()}-{season}-{len(recent)}-{random.random()}")
    return {
        "date": day.strftime("%A, %B %d, %Y"),
        "season": season,
        "holiday": holiday,
        "suggested_palettes": s["palettes"],
        "seasonal_accents": s["accents"],
        "technique": rnd.choice(TECHNIQUES),
        "background": rnd.choice(s["backgrounds"]),
        "shape": rnd.choice(SHAPES),
        "skin": rnd.choice(SKIN_TONES),
        "metal": rnd.choice(METALS),
        "style": "luxe",
        "avoid_recent": [p.get("palette_text", "") + " / " + p.get("technique", "") for p in recent[-30:]],
        "local_phrase": pick_seo(recent),
        "avoid_questions": [p["question"] for p in recent if p.get("question")][-80:],
    }


RECIPE_SYSTEM = f"""You design daily luxury nail-art Instagram posts for {BUSINESS['name']}, a nail salon in {BUSINESS['city']}.
Return ONLY JSON with these keys:
  "title": short name for the look (3-6 words)
  "palette": list of 3-4 specific named colors
  "design": describe the set on the visible nails of ONE hand, vivid and concrete for an image generator; all nails must look like one coordinated salon set (an accent nail on the ring finger is fine)
  "caption": Instagram caption, 2-4 short lines, warm and luxurious and confident, 1-2 tasteful emojis max, describes the colors and design so people want it, presents it as design inspiration or this season's look (never claim it is a real client's set), ends with a call to action to book at {BUSINESS['name']} (link in bio) or call {BUSINESS['phone']}; and work the exact phrase given in local_phrase into one sentence naturally (it helps people find us in Instagram search). Do NOT put hashtags in the caption.
  "question": ONE short, friendly, open-ended question for the end of the caption that invites a genuine reply about nails, this design, colors, seasons, self-care or style (e.g. asking which detail they'd pick, what color they wear in a season, what occasion they'd wear it to). It must be different from every question in avoid_questions. NEVER ask people to like, share, save, tag friends, follow, or comment a specific word/emoji/number, and never promise anything in return for engaging.
  "hashtags": exactly 5 hashtags. 1-2 local from {LOCAL_TAGS}, the rest about this design, shape and season. No repeats of the exact same set used recently.
If style is "french": design a CLASSIC FRENCH TIP set (sheer nude/pink base, crisp tips as given in french_tip, a small accent as given in french_accent on 1-2 nails only); the palette is the base + tip + accent colors; the caption should celebrate a timeless French with a modern twist. Do not describe heavy chrome or big crystals for French sets.
Rules: if a holiday is given, lean into it elegantly (never cartoonish). The palette and technique combination must be different from everything in avoid_recent. You may invent a fresh palette beyond the suggestions as long as it fits the season."""


def make_recipe(client, brief):
    def call(model):
        resp = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": RECIPE_SYSTEM},
                      {"role": "user", "content": json.dumps(brief)}],
        )
        return json.loads(resp.choices[0].message.content)

    for model in TEXT_MODELS:
        try:
            r = with_retries(lambda: call(model), f"text model {model}", tries=2)
            r["text_model"] = model
            return r
        except FatalAPIError:
            raise
        except Exception:  # noqa: BLE001
            continue
    print("  All text models failed - using built-in backup recipe.")
    return fallback_recipe(brief)


def fallback_recipe(brief):
    pal = random.choice(brief["suggested_palettes"])
    return {
        "title": f"{brief['season'].title()} {brief['metal'].title()} Glam",
        "palette": [c.strip() for c in pal.replace(" and ", ", ").split(",")],
        "design": f"Flowing sections of {pal} with {brief['technique']}, outlined in {brief['metal']} chrome "
                  f"with clustered crystals; {brief['seasonal_accents']}.",
        "caption": f"This season's look: {pal} with {brief['metal']} chrome and crystal details ✨\n"
                   f"Want a set like this? Book at {BUSINESS['name']} (link in bio) or call {BUSINESS['phone']}.",
        "hashtags": [],
        "text_model": "backup",
    }


SEO_PHRASES = [
    "nail salon in Modesto", "Modesto nail art", "gel nails in Modesto", "Modesto manicure",
    "acrylic nails in Modesto", "nail spa on McHenry Ave", "Central Valley nail salon",
    "luxury nails in Modesto", "Modesto nail designs", "best nails in Modesto, CA",
]
SEO_FALLBACK = {
    "nail salon in Modesto": "Visit our nail salon in Modesto to make this look yours.",
    "Modesto nail art": "Custom Modesto nail art, made just for you.",
    "gel nails in Modesto": "Looking for gel nails in Modesto? We've got you.",
    "Modesto manicure": "Treat yourself to a Modesto manicure that turns heads.",
    "acrylic nails in Modesto": "Want acrylic nails in Modesto that last? Come see us.",
    "nail spa on McHenry Ave": "Find us at our nail spa on McHenry Ave.",
    "Central Valley nail salon": "Your favorite Central Valley nail salon is ready for you.",
    "luxury nails in Modesto": "Luxury nails in Modesto, done with care.",
    "Modesto nail designs": "New Modesto nail designs every week.",
    "best nails in Modesto, CA": "Come see why clients call these the best nails in Modesto, CA.",
}


def pick_seo(posts):
    last = {}
    for i, p in enumerate(posts):
        if p.get("seo_phrase"):
            last[p["seo_phrase"]] = i
    return sorted(SEO_PHRASES, key=lambda x: (last.get(x, -1), random.random()))[0]


def ensure_seo(caption, phrase):
    """Make sure the caption naturally contains the local search phrase."""
    if phrase.lower() in caption.lower():
        return caption
    return caption.rstrip() + "\n" + SEO_FALLBACK.get(phrase, f"Find us for {phrase}.")


WELCOME_LINES = [
    "New here? Welcome to Venetian Nail Spa! What nail style are you loving lately?",
    "Just found us? Welcome to Venetian Nail Spa! What's your favorite nail shape right now?",
    "New to our page? Welcome! What colors are you wearing on your nails this season?",
    "Welcome to all our new friends! What's your dream nail set?",
    "First time here? Welcome to Venetian Nail Spa! Are you more of a classic or a bold nail person?",
    "So happy you found us! What nail look are you planning next?",
    "New here? Welcome to the Venetian Nail Spa family! What's your must-have nail detail: chrome, gems or art?",
    "Welcome, new friends! What nail trend has caught your eye lately?",
    "Just discovering us? Welcome! What's your go-to manicure style?",
    "New to Venetian Nail Spa? Welcome! Short and sweet or long and glam: what's your style?",
    "Hi and welcome if you're new here! What's the last nail color you fell in love with?",
    "Welcome to Venetian Nail Spa! New here? We'd love to hear your favorite nail vibe.",
]


def pick_welcome(posts):
    """Rotate welcome lines: least recently used first, never the same line twice in a row."""
    last = {}
    for i, p in enumerate(posts):
        if p.get("welcome"):
            last[p["welcome"]] = i
    return sorted(WELCOME_LINES, key=lambda w: (last.get(w, -1), random.random()))[0]

BAIT_WORDS = ("tag ", "tag a", "share this", "share with", "like this post", "like if", "hit like", "double tap", "follow", "save this",
              "comment yes", "comment a", "drop a", "type ", "giveaway", "win ")
QUESTION_ANGLES = [
    "Which nail in this set would you pick as your accent nail?",
    "What occasion would you wear this {title} look to?",
    "Would you wear these {colors} shades long or short?",
    "What's your go-to nail color when {season} rolls around?",
    "Gold accents or silver accents with {colors}: what's your vibe?",
    "Almond, coffin or square: which shape would you choose for this {title} set?",
    "What's one nail trend you'd love to try this {season}?",
    "Matte or glossy: how would you wear {colors}?",
    "Which color in this {title} set catches your eye first?",
    "Would you rock this {title} look for a holiday party or everyday?",
    "What's the boldest nail color you've ever worn?",
    "If you could add one charm or gem to this set, where would it go?",
]


def _norm_q(q):
    return "".join(ch for ch in q.lower() if ch.isalnum())


def is_bait(q):
    ql = " " + q.lower() + " "
    return any(w in ql for w in BAIT_WORDS)


def unique_question(client, recipe, brief, posts):
    """Make sure the caption question is new (never used before) and not engagement bait."""
    used = {_norm_q(p["question"]) for p in posts if p.get("question")}
    q = (recipe.get("question") or "").strip()
    if q and _norm_q(q) not in used and not is_bait(q):
        return q
    if client is not None:
        for model in TEXT_MODELS[:2]:
            for _ in range(2):
                try:
                    resp = client.chat.completions.create(model=model, response_format={"type": "json_object"},
                        messages=[{"role": "user", "content": json.dumps({
                            "task": "Write ONE new short, friendly, open-ended question for a nail salon Instagram caption "
                                    "about this design. Must be different from every question in used_questions. Never ask "
                                    "to like/share/save/tag/follow or to comment a specific word or emoji. Return JSON {\"question\": \"...\"}",
                            "design": recipe.get("title"), "colors": recipe.get("palette"), "season": brief["season"],
                            "used_questions": [p["question"] for p in posts if p.get("question")][-120:]})}])
                    q = json.loads(resp.choices[0].message.content).get("question", "").strip()
                    if q and _norm_q(q) not in used and not is_bait(q):
                        return q
                except Exception:  # noqa: BLE001
                    break
    colors = " and ".join((recipe.get("palette") or ["these"])[:2]).lower()
    for tmpl in random.sample(QUESTION_ANGLES, len(QUESTION_ANGLES)):
        q = tmpl.format(title=recipe.get("title", "this"), colors=colors, season=brief["season"])
        if _norm_q(q) not in used:
            return q
    n = 1
    while True:
        q = f"What would you name this {recipe.get('title', 'look')} set? (#{len(used) + n})"
        if _norm_q(q) not in used:
            return q
        n += 1


def clean_hashtags(tags, season, shape):
    out = []
    for t in tags or []:
        t = "#" + "".join(ch for ch in str(t) if ch.isalnum())
        if t.lower() == "#venetiannailspa":
            t = "#VenetianNailSpaModesto"
        if len(t) > 2 and t.lower() not in [o.lower() for o in out]:
            out.append(t)
    if not any(t.lower() in [l.lower() for l in LOCAL_TAGS] for t in out):
        out.insert(0, random.choice(LOCAL_TAGS[:2]))
    fillers = [f"#{season.title()}Nails", "#LuxuryNails", f"#{shape.title().replace(' ', '')}Nails",
               "#NailArt", "#ChromeNails", "#GelNails"]
    for f in fillers:
        if len(out) >= 5:
            break
        if f.lower() not in [o.lower() for o in out]:
            out.append(f)
    return out[:5]


def nails_text(brief, recipe):
    if brief.get("style") == "french":
        color = (recipe.get("palette") or ["a soft seasonal shade"])[0]
        tip = brief["french_tip"].format(color=color, metal=brief["metal"])
        accent = brief["french_accent"].format(metal=brief["metal"])
        return FRENCH_NAILS.format(shape=brief["shape"], tip=tip, accent=accent)
    return LUXE_NAILS.format(shape=brief["shape"], metal=brief["metal"])


def build_image_prompt(brief, recipe):
    parts = [
        REFERENCE_STYLE.format(pose=(CURRENT_POSE["pose"] or {}).get("desc", "a single relaxed hand"),
                               nails=nails_text(brief, recipe)),
        f"Color palette: {', '.join(recipe['palette'])}.",
        f"Design: {recipe['design']}",
        f"Technique emphasis: {brief['technique']}. Seasonal mood ({brief['season']}): {brief['seasonal_accents']}.",
    ]
    if brief["holiday"]:
        parts.append(f"Subtle elegant nod to {brief['holiday']}.")
    parts.append(f"Background: soft {brief['background']}, out of focus.")
    parts.append(REALISM_RULES.format(skin=brief["skin"]))
    return " ".join(parts)


POSE_PROMPT = (
    "The attached photo is the POSE REFERENCE. Recreate its hand pose, finger positions, finger curl, camera "
    "angle, crop and composition exactly, as a new ultra-realistic photo. Change ONLY these things: the nail "
    "design, the fabric/background, the skin tone and the lighting, as described below. Do not copy the "
    "reference nail design, and never copy any text, website name or watermark from the reference. "
)


def _try_sizes(fn):
    for size in IMAGE_SIZES:
        try:
            return fn(size)
        except Exception as e:  # noqa: BLE001
            if "size" in str(e).lower() and size != IMAGE_SIZES[-1]:
                print(f"  size {size} not supported, trying next size")
                continue
            raise


def make_image(client, prompt):
    """Uses the pose reference photo (image edit) so the pose matches; falls back to text only."""
    pose = CURRENT_POSE["pose"]
    ref = pose["path"].read_bytes() if pose else None

    def with_ref(model):
        def go(size):
            mime = "image/png" if pose["path"].suffix.lower() == ".png" else "image/jpeg"
            kw = dict(model=model, image=(pose["path"].name, ref, mime), prompt=POSE_PROMPT + prompt,
                      size=size, quality=IMAGE_QUALITY, n=1)
            try:
                resp = client.images.edit(**kw, input_fidelity="high")
            except TypeError:
                resp = client.images.edit(**kw)
            except Exception as e:  # noqa: BLE001
                if "input_fidelity" in str(e):
                    resp = client.images.edit(**kw)
                else:
                    raise
            return base64.b64decode(resp.data[0].b64_json)
        return _try_sizes(go)

    def text_only(model):
        def go(size):
            resp = client.images.generate(model=model, prompt=prompt, size=size, quality=IMAGE_QUALITY, n=1)
            return base64.b64decode(resp.data[0].b64_json)
        return _try_sizes(go)

    last = None
    for model in IMAGE_MODELS:
        if ref:
            try:
                return with_retries(lambda: with_ref(model), f"pose-reference image {model}", tries=2), model + "+pose"
            except FatalAPIError:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
        try:
            return with_retries(lambda: text_only(model), f"image model {model}", tries=2), model
        except FatalAPIError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"Image generation failed on all models: {last}")


HAND_CHECK = """You are a strict photo editor checking an AI-generated nail photo before it goes on Instagram.
Image 1 is the POSE REFERENCE. Image 2 is the NEW photo to check.
Reject the new photo (ok=false) if ANY of these are true:
- more than one hand, or any extra/stray fingers or hand parts
- any finger that is fused, duplicated, missing a segment, bent unnaturally, too long/short, or attached wrong
- wrong number of nails, a nail on the wrong place, or a nail cut off by the frame
- the HAND POSE clearly differs from the reference ({pose_desc})
Judge the pose ONLY by hand orientation, finger arrangement/curl and camera angle. The nail design, nail colors,
background, fabric, props, number of rings, skin tone and lighting are SUPPOSED to be different - never reject for those.
- any visible text, logo or watermark
Also write alt text for image 2 for people who are blind or have low vision: 1-2 plain sentences, under 250 characters,
describing what is actually visible (one hand, skin tone, nail shape and length, the colors and design on the nails,
any ring, the fabric/background). No hashtags, no emojis, no marketing words. End with "AI-generated image."
Return ONLY JSON: {"ok": true/false, "pose_match": 1-10, "issues": "short description", "alt_text": "..."}"""


def _data_url(raw, max_h=768):
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    if im.height > max_h:
        im = im.resize((int(im.width * max_h / im.height), max_h))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def alt_from_recipe(brief, recipe):
    return (f"Close-up of one {brief['skin']} hand resting on {brief['background']}, with long {brief['shape']} "
            f"nails in {', '.join(recipe.get('palette', []))} with {brief['metal']} accents. AI-generated image.")[:300]


def check_hand(client, raw):
    """Vision check for anatomy + pose. Returns (ok, notes). If the checker itself fails, accept."""
    pose = CURRENT_POSE["pose"]
    if not pose:
        return True, "no reference"
    content = [{"type": "text", "text": HAND_CHECK.replace("{pose_desc}", pose["desc"][:300])},
               {"type": "image_url", "image_url": {"url": _data_url(pose["path"].read_bytes())}},
               {"type": "image_url", "image_url": {"url": _data_url(raw)}}]
    for model in TEXT_MODELS:
        try:
            resp = client.chat.completions.create(model=model, response_format={"type": "json_object"},
                                                  messages=[{"role": "user", "content": content}])
            r = json.loads(resp.choices[0].message.content)
            ok = bool(r.get("ok")) and int(r.get("pose_match", 0) or 0) >= 7
            check_hand.last_alt = (r.get("alt_text") or "").strip()[:300]
            return ok, f"pose {r.get('pose_match')}/10 - {r.get('issues', '')}"
        except Exception as e:  # noqa: BLE001
            print(f"  hand check with {model} failed: {str(e)[:120]}")
    return True, "checker unavailable"


MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "2"))


def make_checked_image(client, prompt):
    notes = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        budget_check()                       # stop BEFORE spending if a limit is reached
        raw, model = make_image(client, prompt)
        record_image()
        ok, note = check_hand(client, raw)
        print(f"  attempt {attempt}: {'PASS' if ok else 'REJECTED'} ({note})")
        notes.append(note)
        if ok:
            return raw, model, note
    raise RuntimeError("Every image failed the hand check, so nothing was saved or posted: " + " | ".join(notes))


def mock_image(brief, recipe):
    img = Image.new("RGB", (1024, 1536), (245, 240, 235))
    d = ImageDraw.Draw(img)
    for i in range(10):
        x = 120 + (i % 5) * 170
        y = 380 + (i // 5) * 560
        d.ellipse([x, y, x + 110, y + 330], fill=(200 - i * 12, 80 + i * 10, 160), outline=(212, 175, 55), width=8)
    d.rectangle([0, 0, 79, 1535], fill=(255, 0, 0))      # left edge marker (should be cropped away)
    d.rectangle([944, 0, 1023, 1535], fill=(255, 0, 0))  # right edge marker
    d.text((300, 80), f"MOCK {brief['season'].upper()} - {recipe['title']}", fill=(60, 40, 40))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), "mock"


FINE_PRINT = "FOR INSPO PURPOSE ONLY. AI GENERATED IMAGE"
SHOW_FINE_PRINT = os.getenv("SHOW_FINE_PRINT", "0") == "1"          # off: disclosure is in the caption instead
AI_DISCLOSURE = "✨ AI-generated design inspiration"


def _font(size):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def add_fine_print(img: Image.Image, force=False) -> Image.Image:
    if not (SHOW_FINE_PRINT or force):
        return img.convert("RGB")
    """Small white line centered at the very bottom with a soft shadow (no strip)."""
    img = img.convert("RGB")
    w, h = img.size
    size = max(16, w // 50)          # ~21px on a 1080-wide image
    font = _font(size)
    draw = ImageDraw.Draw(img, "RGBA")
    box = draw.textbbox((0, 0), FINE_PRINT, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    pad = size // 2
    strip_top = h - th - pad * 2 - 6
    # no strip - just white text with a soft shadow so it stays readable on light or dark backgrounds
    x, y = (w - tw) / 2 - box[0], strip_top + pad - box[1] + 3
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((x + 1, y + 2), FINE_PRINT, font=font, fill=(0, 0, 0, 170))
    from PIL import ImageFilter
    img = Image.alpha_composite(img.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(2))).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    draw.text((x, y), FINE_PRINT, font=font, fill=(255, 255, 255, 240))
    return img


def to_9x16(raw: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    target = 9 / 16
    if w / h > target:  # too wide -> crop sides
        nw = int(h * target)
        left = (w - nw) // 2
        img = img.crop((left, 0, left + nw, h))
    else:               # too tall -> crop top/bottom
        nh = int(w / target)
        top = (h - nh) // 2
        img = img.crop((0, top, w, top + nh))
    return img.resize((1080, 1920), Image.LANCZOS)


# ---------------------------------------------------------------- schedule
SLOTS = {"morning": 9, "noon": 12, "evening": 18}  # 9 AM, 12 PM and 6 PM Pacific


def current_slot(now=None):
    """GitHub's scheduler often starts runs late (sometimes ~1 hour), so the workflow
    runs every hour. A run picks the next slot starting within 2 hours (made early,
    then published exactly on time), or a slot that was missed less than 1 hour ago."""
    now = now or dt.datetime.now(TZ)
    upcoming, late = None, None
    for name, hour in SLOTS.items():
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        mins = (target - now).total_seconds() / 60
        if 0 <= mins <= 120:
            upcoming = upcoming or name
        elif -60 <= mins < 0:
            late = name
    return upcoming or late


# ---------------------------------------------------------------- main
def make_post(client, day, season, posts, slot=None, force_pose=None):
    holiday = upcoming_holiday(day) if season == season_for(day) else None
    if holiday and random.random() < 0.5:
        holiday = None  # about half the posts near a holiday lean into it
    brief = build_brief(day, season, holiday, posts)
    if os.getenv("FORCE_STYLE") == "french" or (os.getenv("FORCE_STYLE") != "luxe" and want_french(posts)):
        brief["style"] = "french"
        brief["shape"] = random.choice(FRENCH_SHAPES)
        brief["technique"] = "classic French tip with a delicate design accent"
        brief["french_tip"] = random.choice(FRENCH_TIPS)
        brief["french_accent"] = random.choice(FRENCH_ACCENTS)
    CURRENT_POSE["pose"] = force_pose or pick_pose(posts)
    pose_name = CURRENT_POSE["pose"]["name"] if CURRENT_POSE["pose"] else None
    print(f"- {day} | {season} | holiday: {holiday} | pose: {pose_name} | {brief['technique']} | {brief['background']}")

    if MOCK:
        recipe = fallback_recipe(brief)
        recipe["hashtags"] = ["#ModestoNails"]
    else:
        recipe = make_recipe(client, brief)
    tags = clean_hashtags(recipe.get("hashtags"), season, brief["shape"])
    prompt = build_image_prompt(brief, recipe)

    check_hand.last_alt = ""
    if MOCK:
        raw, model = mock_image(brief, recipe)
        check_note = "mock"
    else:
        raw, model, check_note = make_checked_image(client, prompt)
    alt_text = check_hand.last_alt or alt_from_recipe(brief, recipe)
    img = to_9x16(raw)

    stamp = dt.datetime.now(TZ).strftime("%H%M%S")
    pid = f"{day.isoformat()}-{season}-{stamp}-{hashlib.md5(prompt.encode()).hexdigest()[:5]}"
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    # 9:16 version = Instagram/Facebook Story (no caption there), so it carries the AI note
    add_fine_print(img, force=True).save(POSTS_DIR / f"{pid}.jpg", "JPEG", quality=92, optimize=True)
    # Instagram feed posts only accept 4:5 up to 1.91:1, so also save a 4:5 (1080x1350) version
    feed = img.crop((0, (1920 - 1350) // 2, 1080, (1920 - 1350) // 2 + 1350))  # hand is centered
    add_fine_print(feed).save(POSTS_DIR / f"{pid}-feed.jpg", "JPEG", quality=92, optimize=True)

    recipe["caption"] = ensure_seo(recipe["caption"], brief["local_phrase"])
    question = unique_question(client, recipe, brief, posts)
    welcome = pick_welcome(posts)
    caption = recipe["caption"].strip() + "\n\n" + question + "\n\n" + welcome + "\n\n" + AI_DISCLOSURE
    return {
        "id": pid,
        "date": day.isoformat(),
        "created": dt.datetime.now(TZ).isoformat(timespec="seconds"),
        "season": season,
        "holiday": holiday,
        "title": recipe.get("title", ""),
        "palette": recipe.get("palette", []),
        "palette_text": ", ".join(recipe.get("palette", [])),
        "technique": brief["technique"],
        "background": brief["background"],
        "image": f"posts/{pid}.jpg",
        "feed_image": f"posts/{pid}-feed.jpg",
        "slot": slot,
        "instagram": None,
        "caption": caption,
        "hashtags": tags,
        "full_caption": caption + "\n.\n" + " ".join(tags),
        "alt_text": alt_text,
        "question": question,
        "welcome": welcome,
        "seo_phrase": brief["local_phrase"],
        "style": brief.get("style", "luxe"),
        "image_model": model,
        "hand_check": check_note,
        "pose": pose_name,
        "text_model": recipe.get("text_model", ""),
        "prompt": prompt,
    }


def main():
    mode = os.getenv("MODE", "none").strip().lower() or "none"
    if mode not in ("slot", "one", "samples", "test", "test-poses"):
        print(f"Mode '{mode}': nothing to make (safety default).")
        return
    force = os.getenv("FORCE_SEASON", "").strip().lower()
    today = dt.datetime.now(TZ).date()
    posts = load_posts()
    client = None if MOCK else openai_client()

    slot = None
    if mode == "slot":
        slot = current_slot()
        if not slot:
            print("Not near 9 AM or 6 PM Pacific - nothing to do.")
            return
        if any(p.get("date") == today.isoformat() and p.get("slot") == slot for p in posts):
            print(f"The {slot} post for today already exists - nothing to do.")
            return

    if mode in ("one", "test", "samples"):
        manual_today = [p for p in posts if p.get("date") == today.isoformat() and not p.get("slot")]
        need = {"one": 1, "test": 2, "samples": 4}[mode]
        if len(manual_today) + need > MANUAL_DAILY_LIMIT and mode == "one":
            msg = f"Manual limit reached ({len(manual_today)} made today, max {MANUAL_DAILY_LIMIT}). Try tomorrow."
            print(msg); write_status(False, "Safety stop: " + msg); sys.exit(1)

    if mode == "slot":
        cutoff = dt.datetime.now(TZ) - dt.timedelta(hours=24)
        waiting = [p for p in posts if not p.get("test") and p.get("slot")
                   and (not p.get("instagram") or p["instagram"].get("error"))
                   and dt.datetime.fromisoformat(p["created"]) > cutoff]
        if waiting:
            p = waiting[-1]
            print(f"Re-using unposted image {p['id']} instead of paying for a new one.")
            p["slot"] = slot
            p["date"] = today.isoformat()
            save_posts(posts)
            (ROOT / ".last_run.json").write_text(json.dumps({"ids": [p["id"]], "slot": slot}))
            write_status(True, "Re-used an unposted image (no new cost).")
            return

    if mode == "one" and posts:
        try:
            last = dt.datetime.fromisoformat(posts[-1]["created"])
            mins = (dt.datetime.now(TZ) - last).total_seconds() / 60
            if mins < COOLDOWN_MIN:
                print(f"Last image was made {mins:.0f} min ago - skipping to avoid a double-click duplicate.")
                write_status(True, f"Skipped: last image was only {mins:.0f} min ago (wait {COOLDOWN_MIN} min).")
                return
        except Exception:  # noqa: BLE001
            pass

    if mode == "samples":
        jobs = [(today, s) for s in ("winter", "spring", "summer", "fall")]
    elif mode == "test":
        jobs = [(today, season_for(today))] * 2
    elif mode == "test-poses":
        tried = {p.get("pose") for p in posts}
        new_poses = [p for p in load_poses() if not p["name"].startswith("01-") and p["name"] not in tried]
        jobs = [(today, season_for(today))] * len(new_poses)
    else:
        jobs = [(today, force if force in SEASONS else season_for(today))]
    TESTING["on"] = mode in ("test", "test-poses")

    made = 0
    new_ids = []
    failed_poses = []
    try:
        for day, season in jobs:
            if mode == "test-poses":
                pose_now = new_poses[len(new_ids) + len(failed_poses)]
                try:
                    post = make_post(client, day, season, posts, slot, force_pose=pose_now)
                except BudgetStop:
                    raise
                except Exception as e:  # noqa: BLE001
                    failed_poses.append(pose_now["name"])
                    print(f"  pose {pose_now['name']} failed: {str(e)[:160]}")
                    continue
            else:
                post = make_post(client, day, season, posts, slot)
            post["quality"] = IMAGE_QUALITY
            if mode in ("test", "test-poses"):
                post["test"] = True              # test images are never posted
            posts.append(post)
            new_ids.append(post["id"])
            save_posts(posts)
            made += 1
        (ROOT / ".last_run.json").write_text(json.dumps({"ids": new_ids, "slot": slot}))
        write_status(True, f"Made {made} new post(s)." + (f" Poses that failed: {', '.join(failed_poses)}" if failed_poses else ""))
        print(f"Done: {made} post(s).")
    except BudgetStop as e:
        save_posts(posts)
        write_status(False, f"Safety stop: {e}")
        print(f"SAFETY STOP: {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        save_posts(posts)
        write_status(False, f"Made {made} post(s), then failed: {type(e).__name__}: {str(e)[:300]}")
        print(f"FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
