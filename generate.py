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

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
POSTS_DIR = DOCS / "posts"
POSTS_JSON = DOCS / "posts.json"
STATUS_JSON = DOCS / "status.json"
TZ = ZoneInfo("America/Los_Angeles")

IMAGE_MODELS = [m for m in os.getenv("IMAGE_MODELS", "gpt-image-2,gpt-image-1.5,gpt-image-1").split(",") if m]
TEXT_MODELS = [m for m in os.getenv("TEXT_MODELS", "gpt-5-mini,gpt-4.1-mini,gpt-4o-mini").split(",") if m]
IMAGE_QUALITY = os.getenv("IMAGE_QUALITY", "high")
MOCK = os.getenv("MOCK") == "1"

BUSINESS = {
    "name": "Venetian Nail Spa",
    "city": "Modesto, CA",
    "phone": "209-408-8597",
    "booking": "https://book.gocheckin.net/v3/nails/venetian-nail-spa-17058/choose-service",
}

LOCAL_TAGS = ["#ModestoNails", "#VenetianNailSpa", "#ModestoCA", "#CentralValleyNails"]

# ---------------------------------------------------------------- style
REFERENCE_STYLE = (
    "Ultra-realistic luxury beauty-editorial macro photograph. Two elegant feminine hands softly "
    "overlapped in a relaxed natural pose, nails as the clear hero of the shot. Long {shape} nails, "
    "perfectly manicured, clean cuticles, flawless high-gloss gel finish. Each nail is divided into "
    "flowing curved 'stained glass' sections outlined in raised {metal} chrome lines, filled with "
    "rich color, with small clustered rhinestones and crystals placed along the metallic lines. "
    "Every nail is slightly different but the full set is one coordinated design."
)

REALISM_RULES = (
    "Anatomically correct hands: exactly five fingers on each hand, natural knuckles, realistic skin "
    "texture and pores, natural proportions, {skin} skin tone. Nails razor sharp and in focus, shallow "
    "depth of field on the background. Soft warm studio lighting with gentle specular highlights on the "
    "gloss. Tall vertical composition with the hands centered and empty space on the left and right "
    "edges (the image will be cropped to 9:16). No text, no letters, no logos, no watermarks, no brand names."
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
        "backgrounds": ["white faux fur", "champagne silk", "dark emerald velvet", "white marble with gold veins"],
    },
    "spring": {
        "palettes": ["pastel lavender, blush pink and pearl", "mint, butter yellow and white",
                     "soft peach, lilac and gold", "baby blue, blush and pearl chrome",
                     "sage green, cream and rose gold"],
        "accents": "soft florals, pearls, delicate French tips with a twist",
        "backgrounds": ["cream satin", "white marble", "blush pink silk", "white faux fur with a few petals"],
    },
    "summer": {
        "palettes": ["coral, turquoise and gold", "hot pink, citrus orange and gold",
                     "ocean blue, aqua and pearl", "neon pink, lilac and chrome", "sunset orange, magenta and gold"],
        "accents": "tropical and ocean themes, chrome and aura effects, a touch of neon",
        "backgrounds": ["white marble", "champagne silk", "cream satin", "white faux fur"],
    },
    "fall": {
        "palettes": ["burgundy, burnt orange and gold", "mocha, caramel and champagne",
                     "deep plum, olive and gold", "tortoiseshell, espresso and gold leaf",
                     "copper, wine red and cream"],
        "accents": "tortoiseshell, gold leaf, cat-eye shimmer, warm metallics",
        "backgrounds": ["mocha velvet", "cream faux fur", "champagne silk", "dark marble with gold veins"],
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


def with_retries(fn, label, tries=3):
    last = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
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
        "avoid_recent": [p.get("palette_text", "") + " / " + p.get("technique", "") for p in recent[-30:]],
    }


RECIPE_SYSTEM = f"""You design daily luxury nail-art Instagram posts for {BUSINESS['name']}, a nail salon in {BUSINESS['city']}.
Return ONLY JSON with these keys:
  "title": short name for the look (3-6 words)
  "palette": list of 3-4 specific named colors
  "design": 2-3 sentences describing exactly what is painted on the nails (sections, finishes, glitter, accents), vivid and concrete for an image generator
  "caption": Instagram caption, 2-4 short lines, warm and luxurious and confident, 1-2 tasteful emojis max, describes the colors and design so people want it, presents it as design inspiration or this season's look (never claim it is a real client's set), ends with a call to action to book at {BUSINESS['name']} (link in bio) or call {BUSINESS['phone']}; mention Modesto now and then. Do NOT put hashtags in the caption.
  "hashtags": exactly 5 hashtags. 1-2 local from {LOCAL_TAGS}, the rest about this design, shape and season. No repeats of the exact same set used recently.
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


def clean_hashtags(tags, season, shape):
    out = []
    for t in tags or []:
        t = "#" + "".join(ch for ch in str(t) if ch.isalnum())
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


def build_image_prompt(brief, recipe):
    parts = [
        REFERENCE_STYLE.format(shape=brief["shape"], metal=brief["metal"]),
        f"Color palette: {', '.join(recipe['palette'])}.",
        f"Design: {recipe['design']}",
        f"Technique emphasis: {brief['technique']}. Seasonal mood ({brief['season']}): {brief['seasonal_accents']}.",
    ]
    if brief["holiday"]:
        parts.append(f"Subtle elegant nod to {brief['holiday']}.")
    parts.append(f"Background: soft {brief['background']}, out of focus.")
    parts.append(REALISM_RULES.format(skin=brief["skin"]))
    return " ".join(parts)


def make_image(client, prompt):
    def call(model):
        resp = client.images.generate(model=model, prompt=prompt, size="1024x1536",
                                      quality=IMAGE_QUALITY, n=1)
        return base64.b64decode(resp.data[0].b64_json)

    last = None
    for model in IMAGE_MODELS:
        try:
            return with_retries(lambda: call(model), f"image model {model}", tries=3), model
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"Image generation failed on all models: {last}")


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
SLOTS = {"morning": 9, "evening": 18}  # 9 AM and 6 PM Pacific


def current_slot(now=None):
    """GitHub's clock is UTC and ignores daylight saving, so the workflow runs at
    two UTC times per slot. Only the run that lands 8:00-10:59 (or 5:00-7:59 PM)
    Pacific does the work."""
    now = now or dt.datetime.now(TZ)
    for name, hour in SLOTS.items():
        if hour - 1 <= now.hour <= hour + 1:
            return name
    return None


# ---------------------------------------------------------------- main
def make_post(client, day, season, posts, slot=None):
    holiday = upcoming_holiday(day) if season == season_for(day) else None
    if holiday and random.random() < 0.5:
        holiday = None  # about half the posts near a holiday lean into it
    brief = build_brief(day, season, holiday, posts)
    print(f"- {day} | {season} | holiday: {holiday} | {brief['technique']} | {brief['background']}")

    if MOCK:
        recipe = fallback_recipe(brief)
        recipe["hashtags"] = ["#ModestoNails"]
    else:
        recipe = make_recipe(client, brief)
    tags = clean_hashtags(recipe.get("hashtags"), season, brief["shape"])
    prompt = build_image_prompt(brief, recipe)

    raw, model = mock_image(brief, recipe) if MOCK else make_image(client, prompt)
    img = to_9x16(raw)

    stamp = dt.datetime.now(TZ).strftime("%H%M%S")
    pid = f"{day.isoformat()}-{season}-{stamp}-{hashlib.md5(prompt.encode()).hexdigest()[:5]}"
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    img.save(POSTS_DIR / f"{pid}.jpg", "JPEG", quality=92, optimize=True)
    # Instagram feed posts only accept 4:5 up to 1.91:1, so also save a 4:5 (1080x1350) version
    feed = img.crop((0, (1920 - 1350) // 2, 1080, (1920 - 1350) // 2 + 1350))
    feed.save(POSTS_DIR / f"{pid}-feed.jpg", "JPEG", quality=92, optimize=True)

    caption = recipe["caption"].strip()
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
        "image_model": model,
        "text_model": recipe.get("text_model", ""),
        "prompt": prompt,
    }


def main():
    mode = os.getenv("MODE", "one").strip().lower() or "one"
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

    if mode == "samples":
        jobs = [(today, s) for s in ("winter", "spring", "summer", "fall")]
    else:
        jobs = [(today, force if force in SEASONS else season_for(today))]

    made = 0
    new_ids = []
    try:
        for day, season in jobs:
            post = make_post(client, day, season, posts, slot)
            posts.append(post)
            new_ids.append(post["id"])
            save_posts(posts)
            made += 1
        (ROOT / ".last_run.json").write_text(json.dumps({"ids": new_ids, "slot": slot}))
        write_status(True, f"Made {made} new post(s).")
        print(f"Done: {made} post(s).")
    except Exception as e:  # noqa: BLE001
        save_posts(posts)
        write_status(False, f"Made {made} post(s), then failed: {type(e).__name__}: {str(e)[:300]}")
        print(f"FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
