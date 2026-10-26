import os
import re
import io
import html
import smtplib
import hashlib
import logging
import calendar
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid

import feedparser

try:  # Pillow is only needed for article photos; the digest works without it.
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    Image = ImageOps = None

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

MASTHEAD_URL = "https://cdn.jsdelivr.net/gh/mohamed-ashif-m/tech-bites@main/assets/masthead.png"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IST = timezone(timedelta(hours=5, minutes=30))
FIRST_ISSUE = date(2026, 10, 24)  # used for the "No. N" issue counter

# Official sources and established technical news sources.
FEEDS = [
    # ---- Open source / dev platforms ----
    {"name": "GitHub Blog", "url": "https://github.blog/feed/",
     "category": "Open Source", "base_score": 2},

    # ---- Security ----
    {"name": "GitHub Security Advisories", "url": "https://github.com/advisories.atom",
     "category": "Security", "base_score": 4},
    {"name": "CISA Advisories", "url": "https://www.cisa.gov/cybersecurity-advisories/all.xml",
     "category": "Security", "base_score": 4},
    {"name": "BleepingComputer", "url": "https://www.bleepingcomputer.com/feed/",
     "category": "Security", "base_score": 2},
    {"name": "Krebs on Security", "url": "https://krebsonsecurity.com/feed/",
     "category": "Security", "base_score": 3},
    {"name": "Google Security Blog", "url": "https://security.googleblog.com/feeds/posts/default",
     "category": "Security", "base_score": 3},
    {"name": "The Hacker News", "url": "https://feeds.feedburner.com/TheHackersNews",
     "category": "Security", "base_score": 2},

    # ---- General tech news ----
    {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index",
     "category": "Tech News", "base_score": 2},
    {"name": "MIT Technology Review", "url": "https://www.technologyreview.com/feed/",
     "category": "Tech News", "base_score": 3},
    {"name": "IEEE Spectrum", "url": "https://spectrum.ieee.org/feeds/feed.rss",
     "category": "Tech News", "base_score": 2},
    {"name": "BBC Technology", "url": "https://feeds.bbci.co.uk/news/technology/rss.xml",
     "category": "Tech News", "base_score": 2},
    {"name": "The Register", "url": "https://www.theregister.com/headlines.atom",
     "category": "Tech News", "base_score": 1},

    # ---- AI ----
    {"name": "OpenAI News", "url": "https://openai.com/news/rss.xml",
     "category": "AI", "base_score": 3},
    {"name": "Google AI Blog", "url": "https://blog.google/technology/ai/rss/",
     "category": "AI", "base_score": 2},
    {"name": "Hugging Face Blog", "url": "https://huggingface.co/blog/feed.xml",
     "category": "AI", "base_score": 2},
    {"name": "NVIDIA Blog", "url": "https://blogs.nvidia.com/feed/",
     "category": "AI", "base_score": 1},

    # ---- Engineering / cloud ----
    {"name": "Cloudflare Blog", "url": "https://blog.cloudflare.com/rss/",
     "category": "Infrastructure", "base_score": 2},
    {"name": "AWS News Blog", "url": "https://aws.amazon.com/blogs/aws/feed/",
     "category": "Infrastructure", "base_score": 1},
    {"name": "InfoQ", "url": "https://feed.infoq.com/",
     "category": "Engineering", "base_score": 1},

    # ---- India & policy ----
    {"name": "MEDIANAMA", "url": "https://www.medianama.com/feed/",
     "category": "India & Policy", "base_score": 2},

    # ---- Community signal (aggregator, so lowest base score) ----
    {"name": "Hacker News (200+ points)", "url": "https://hnrss.org/frontpage?points=200",
     "category": "Community", "base_score": 0},
]

# Higher scores mean greater priority. These rules are only heuristics.
KEYWORDS = {
    # Security
    "critical": 6,
    "actively exploited": 7,
    "known exploited": 7,
    "zero-day": 6,
    "zero day": 6,
    "remote code execution": 6,
    "rce vulnerability": 6,
    "privilege escalation": 4,
    "authentication bypass": 5,
    "supply chain attack": 5,
    "malicious package": 6,
    "security advisory": 4,
    "vulnerability": 3,
    "security update": 3,
    "patch": 2,
    "reverse engineering": 3,
    # General engineering / open source
    "release": 2,
    "open source": 1,
    "open-source": 1,
    "developer tools": 1,
    # AI
    "artificial intelligence": 2,
    "machine learning": 2,
    "large language model": 2,
    "open-source model": 3,
    "llm": 1,
    # Hardware, infrastructure, policy
    "semiconductor": 3,
    "chip": 2,
    "quantum": 3,
    "data center": 2,
    "regulation": 2,
    "outage": 3,
}

MAX_STORIES = 30
MAX_PER_CATEGORY = 4
LOOKBACK_HOURS = 24

# ---- Article photos ----
MAX_IMAGES = 22          # photos placed in one issue (~35 KB each after processing)
IMAGE_CANDIDATES = 30    # try to find a photo for every selected story
IMAGE_SHARE = 0.6        # in each section ~60% of stories get a picture; the other ~40% are text-only
MIN_SRC_WIDTH, MIN_SRC_HEIGHT = 300, 160   # skip logos, icons and thumbnails
MAX_DOWNLOAD_BYTES = 3_000_000
FETCH_USER_AGENT = "Mozilla/5.0 (compatible; DailyTechBriefing/1.0)"
BAD_IMAGE_HINTS = (
    "pixel", "1x1", "spacer", "doubleclick", "feedburner", "tracking",
    "/ads/", "emoji", "avatar", "gravatar", ".svg", ".gif",
)

# ---- Look & feel ----
INK = "#1B1712"
PAPER = "#F4EAD3"
DESK = "#D9CBA8"
RULE_SOFT = "#B9A98A"
RED_INK = "#8B1A1A"


# --------------------------------------------------------------------------
# Feed collection
# --------------------------------------------------------------------------
def _truncate(text, limit=220):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def clean_text(value):
    """Remove HTML tags and normalize whitespace."""
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = re.sub(r"\s+", " ", html.unescape(value)).strip()
    # WordPress feeds append "The post X appeared first on Y."
    return re.sub(r"\s*The post .{0,300}? appeared first on .*$", "", value).strip()


def parse_date(entry):
    """Return a timezone-aware publication timestamp when available."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    return None


def score_story(title, description, base_score):
    text = f"{title} {description}".lower()
    score = base_score
    matched = []

    for keyword, points in KEYWORDS.items():
        if keyword in text:
            score += points
            matched.append(keyword)

    return score, matched


def story_id(url, title):
    value = url.strip() or title.strip()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sort_key(story):
    return (
        story["score"],
        story["published"] or datetime.min.replace(tzinfo=timezone.utc),
    )


def select_stories(stories):
    """Pick the best stories while keeping every category represented.

    Pass 1: take the top MAX_PER_CATEGORY stories from each category.
    Pass 2: if there is room left under MAX_STORIES, fill it with the
    highest-scoring leftovers regardless of category.
    """
    ranked = sorted(stories, key=_sort_key, reverse=True)

    per_category = {}
    chosen, leftovers = [], []

    for story in ranked:
        count = per_category.get(story["category"], 0)
        if count < MAX_PER_CATEGORY:
            chosen.append(story)
            per_category[story["category"]] = count + 1
        else:
            leftovers.append(story)

    # If categories alone exceed the overall cap, keep the best overall.
    if len(chosen) > MAX_STORIES:
        chosen = chosen[:MAX_STORIES]
    else:
        chosen.extend(leftovers[: MAX_STORIES - len(chosen)])

    chosen.sort(key=_sort_key, reverse=True)
    return chosen


# --------------------------------------------------------------------------
# Finding and preparing article photos
# --------------------------------------------------------------------------
def _good_image_url(url):
    if not url or not url.startswith(("http://", "https://")):
        return False
    low = url.lower()
    return not any(hint in low for hint in BAD_IMAGE_HINTS)


def image_from_entry(entry):
    """Look for a photo URL inside the feed entry itself (no extra requests)."""
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key, []) or []:
            kind = (media.get("medium") or media.get("type") or "image").lower()
            if kind.startswith(("video", "audio")):
                continue
            url = (media.get("url") or "").strip()
            if _good_image_url(url):
                return url

    for enc in entry.get("enclosures", []) or []:
        if (enc.get("type") or "").lower().startswith("image"):
            url = (enc.get("href") or enc.get("url") or "").strip()
            if _good_image_url(url):
                return url

    blobs = [c.get("value", "") for c in entry.get("content", []) or []]
    blobs.append(entry.get("summary", "") or "")
    for blob in blobs:
        for match in re.finditer(r"<img[^>]+src=[\"']([^\"']+)", blob, re.I):
            url = html.unescape(match.group(1)).strip()
            if _good_image_url(url):
                return url

    return None


def _http_get(url, timeout, limit):
    request = urllib.request.Request(
        url, headers={"User-Agent": FETCH_USER_AGENT, "Accept": "*/*"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        return content_type, response.read(limit)


def og_image(page_url):
    """Fallback: read the article page's social-preview image (og:image)."""
    try:
        _, data = _http_get(page_url, timeout=8, limit=300_000)
        text = data.decode("utf-8", "ignore")
        patterns = (
            r"<meta[^>]+property=[\"']og:image(?::secure_url)?[\"'][^>]+content=[\"']([^\"']+)",
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:image",
            r"<meta[^>]+name=[\"']twitter:image[\"'][^>]+content=[\"']([^\"']+)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                url = urllib.parse.urljoin(page_url, html.unescape(match.group(1)).strip())
                if _good_image_url(url):
                    return url
    except Exception as exc:
        logging.info("og:image lookup failed for %s (%s)", page_url, exc)
    return None


def download_image(url):
    """Return raw image bytes if the URL is a usable, large-enough photo."""
    try:
        content_type, data = _http_get(url, timeout=10, limit=MAX_DOWNLOAD_BYTES)
        if not content_type.lower().startswith("image/") or "svg" in content_type.lower():
            return None
        with Image.open(io.BytesIO(data)) as probe:
            if probe.width < MIN_SRC_WIDTH or probe.height < MIN_SRC_HEIGHT:
                return None
        return data
    except Exception as exc:
        logging.info("Image download failed for %s (%s)", url, exc)
        return None


def fetch_images(stories):
    """Download one photo for as many top stories as possible.

    Returns {story_id: raw_bytes}, limited to MAX_IMAGES in rank order.
    """
    if Image is None:
        logging.warning("Pillow is not installed; sending the digest without photos.")
        return {}

    def candidate_urls(story):
        if story.get("image_url"):
            yield story["image_url"]
        yield og_image(story["url"])  # only reached if the feed image failed

    def work(story):
        tried = set()
        for url in candidate_urls(story):
            if not url or url in tried:
                continue
            tried.add(url)
            data = download_image(url)
            if data:
                return story["id"], data
        return story["id"], None

    candidates = stories[:IMAGE_CANDIDATES]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(pool.map(work, candidates))

    images = {}
    for story in candidates:  # keep rank order
        data = results.get(story["id"])
        if data and len(images) < MAX_IMAGES:
            images[story["id"]] = data

    logging.info("Found photos for %d of %d candidate stories", len(images), len(candidates))
    return images


def vintage_jpeg(data, size):
    """Crop to `size`, convert to a warm sepia print look, return JPEG bytes."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        image = ImageOps.fit(image, size, Image.LANCZOS, centering=(0.5, 0.35))
        gray = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
        image = ImageOps.colorize(gray, black="#1B1208", mid="#8A6F4D", white="#F2E4C4")
        out = io.BytesIO()
        image.save(out, "JPEG", quality=72, optimize=True, progressive=True)
        return out.getvalue()
    except Exception as exc:
        logging.info("Could not process image (%s)", exc)
        return None


def collect_stories():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    stories = []
    seen = set()

    for feed in FEEDS:
        try:
            parsed_feed = feedparser.parse(
                feed["url"],
                request_headers={"User-Agent": "DailyTechBriefing/1.0"},
            )

            if parsed_feed.bozo and not parsed_feed.entries:
                logging.warning("Could not read feed: %s", feed["name"])
                continue

            logging.info(
                "%s: fetched %d entries",
                feed["name"],
                len(parsed_feed.entries),
            )

            for entry in parsed_feed.entries:
                title = clean_text(entry.get("title", "Untitled"))
                url = entry.get("link", "").strip()

                if not url:
                    continue

                unique_id = story_id(url, title)
                if unique_id in seen:
                    continue
                seen.add(unique_id)

                published = parse_date(entry)

                # Keep recent stories; retain undated stories but rank them
                # lower because their freshness cannot be verified.
                if published and published < cutoff:
                    continue

                description = clean_text(
                    entry.get("summary", "")
                    or entry.get("description", "")
                )[:700]

                score, matched = score_story(
                    title, description, feed["base_score"]
                )

                if not published:
                    score -= 2

                stories.append({
                    "id": unique_id,
                    "title": title,
                    "url": url,
                    "source": feed["name"],
                    "category": feed["category"],
                    "description": description,
                    "published": published,
                    "score": score,
                    "matched": matched,
                    "image_url": image_from_entry(entry),
                })

        except Exception:
            logging.exception("Failed to fetch %s", feed["name"])

    return select_stories(stories)


# --------------------------------------------------------------------------
# Newspaper-style email
# --------------------------------------------------------------------------
# Headline/body fonts: web fonts load in Apple Mail and iOS; Gmail and Outlook
# fall back to Georgia / Times, which still read as classic newsprint.
HEAD_FONT = "'Playfair Display','Old Standard TT',Georgia,'Times New Roman',serif"
BODY_FONT = "'Old Standard TT',Georgia,'Palatino Linotype','Times New Roman',serif"

CSS = f"""
body{{margin:0;padding:0;background:{DESK}}}
a{{color:{INK}}}
.head{{font-family:{HEAD_FONT};color:{INK};font-weight:900;text-decoration:none}}
.body{{font-family:{BODY_FONT};color:#2B251D;font-size:13.5px;line-height:1.5;text-align:justify;margin:6px 0 0}}
.kick{{font-family:{BODY_FONT};font-size:10.5px;letter-spacing:1.6px;text-transform:uppercase;color:#6B5E48;font-weight:700}}
.more{{font-family:{BODY_FONT};font-size:12px;font-style:italic;font-weight:700;color:{INK};text-decoration:underline}}
.sec{{font-family:{HEAD_FONT};font-size:15px;letter-spacing:5px;text-transform:uppercase;font-weight:900;color:{INK};text-align:center}}
.meta{{font-family:{BODY_FONT};font-size:11px;letter-spacing:1px;text-transform:uppercase;color:{INK}}}
@media only screen and (max-width:640px){{
  .wrap{{width:100%!important}}
  .pad{{padding-left:16px!important;padding-right:16px!important}}
  .col{{display:block!important;width:100%!important;box-sizing:border-box!important}}
  .coll{{padding-right:0!important}}
  .colr{{border-left:none!important;padding-left:0!important}}
  .fluid{{width:100%!important;max-width:none!important;height:auto!important}}
  .lead{{font-size:26px!important;line-height:1.15!important}}
  .meta{{font-size:9.5px!important;letter-spacing:.5px!important}}
}}
"""


def _fmt_dt(value):
    if not value:
        return "Undated"
    return value.astimezone(IST).strftime("%d %b, %H:%M IST")


def make_email(stories, images=None):
    """Build the newspaper HTML. Returns (html, inline_images).

    inline_images is a list of (content_id, bytes, subtype) to attach as
    related parts; the HTML refers to them with cid: URLs.
    """
    images = images or {}
    inline = []
    esc = html.escape
    now = datetime.now(IST)
    today = now.strftime("%A, %d %B %Y")
    issue_no = max(1, (now.date() - FIRST_ISSUE).days + 1)

    max_score = max((s["score"] for s in stories), default=0) or 1

    # The lead: best-ranked story among the top few that has a photo.
    lead = None
    for story in stories[:6]:
        if story["id"] in images:
            lead = story
            break
    if lead is None and stories:
        lead = stories[0]

    sections = {}
    for story in stories:
        if story is not lead:
            sections.setdefault(story["category"], []).append(story)

    def cid_for(data, subtype, filename):
        cid = make_msgid(domain="techbites.local")
        inline.append((cid, data, subtype, filename))
        return cid[1:-1]

    def photo(story, size, shown_width):
        """Sepia print of the story's photo, or "" if it has none."""
        raw = images.get(story["id"])
        jpeg = vintage_jpeg(raw, size) if raw else None
        if not jpeg:
            return ""
        cid = cid_for(jpeg, "jpeg", f"photo-{len(inline)}.jpg")
        return (
            f"<img src='cid:{cid}' width='{shown_width}' "
            f"alt='Photo: {esc(story['source'], quote=True)}' "
            f"class='fluid' style='display:block;width:100%;max-width:{shown_width}px;"
            f"height:auto;border:1px solid {INK};margin:0 0 8px'>"
        )

    def kicker(story, with_category=False):
        parts = [esc(story["source"]), _fmt_dt(story["published"])]
        if with_category:
            parts.insert(1, esc(story["category"]))
        text = " · ".join(parts)
        if story["score"] / max_score >= 0.66:
            text = f"<span style='color:{RED_INK}'>● Priority</span> · " + text
        return f"<div class='kick'>{text}</div>"

    def summary(story, limit):
        if story["description"]:
            return esc(_truncate(story["description"], limit))
        return "Open the original link for the full details."

    p = []
    p.append(
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='color-scheme' content='light only'>"
        "<meta name='supported-color-schemes' content='light only'>"
        "<title>The Tech Bites Daily</title>"
        "<link href='https://fonts.googleapis.com/css2?family=Old+Standard+TT:ital,wght@0,400;0,700;1,400"
        "&family=Playfair+Display:wght@700;900&display=swap' rel='stylesheet'>"
        f"<style>{CSS}</style></head>"
        f"<body style='margin:0;padding:0;background:{DESK}'>"
    )
    p.append(
        "<div style='display:none;max-height:0;overflow:hidden;opacity:0'>"
        f"{len(stories)} dispatches across {len(sections) + (1 if lead else 0)} sections · {esc(today)}"
        "</div>"
    )
    p.append(
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='background:{DESK}'>"
        "<tr><td align='center' style='padding:20px 10px'>"
        f"<table role='presentation' class='wrap' width='680' cellpadding='0' cellspacing='0' "
        f"style='width:100%;max-width:680px;background:{PAPER};border:1px solid {INK}'>"
    )

    # ---- Masthead ----
    masthead = (
        f"<img src='{html.escape(MASTHEAD_URL, quote=True)}' width='616' alt='The Tech Bites Daily' class='fluid' "
        "style='display:block;width:100%;max-width:616px;height:auto;border:0;margin:0 auto'>"
    )
    p.append(f"<tr><td class='pad' style='padding:18px 32px 0'>{masthead}</td></tr>")

    # ---- Dateline ----
    p.append(
        f"<tr><td class='pad' style='padding:6px 32px 0'>"
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        f"style='border-bottom:1px solid {INK}'><tr>"
        f"<td class='meta' align='left' style='padding:4px 0'>{esc(today)}</td>"
        f"<td class='meta' align='center' style='padding:4px 0'>Vol. I · No. {issue_no}</td>"
        f"<td class='meta' align='right' style='padding:4px 0'>{len(stories)} dispatches</td>"
        "</tr></table></td></tr>"
    )

    if not stories:
        p.append(
            f"<tr><td class='pad' style='padding:40px 32px;text-align:center'>"
            f"<div class='head' style='font-size:22px'>No dispatches today</div>"
            f"<p class='body' style='text-align:center'>Either there was no new coverage "
            "or a feed was unavailable.</p></td></tr>"
        )

    # ---- Lead story ----
    if lead:
        url = esc(lead["url"], quote=True)
        p.append(
            "<tr><td class='pad' style='padding:18px 32px 6px'>"
            f"{kicker(lead, with_category=True)}"
            f"<a href='{url}' class='head lead' style='display:block;margin:6px 0 12px;color:{INK};"
            f"font-size:32px;line-height:1.12'>{esc(lead['title'])}</a>"
            f"{photo(lead, (960, 480), 616)}"
            f"<p class='body' style='font-size:15px'>{summary(lead, 420)}</p>"
            f"<p style='margin:10px 0 0'><a href='{url}' class='more' style='color:{INK}'>Continue reading →</a></p>"
            "</td></tr>"
        )

    # ---- Sections ----
    def cell(story, wide=False, with_photo=True):
        url = esc(story["url"], quote=True)
        img = ""
        if with_photo:
            img = photo(story, (960, 400), 616) if wide else photo(story, (480, 320), 280)
        size = 22 if wide else 18
        # text-only stories have more room, so they can carry a longer summary
        limit = (300 if wide else 220) if not with_photo else (260 if wide else 170)
        return (
            f"{img}{kicker(story)}"
            f"<a href='{url}' class='head' style='display:block;margin:5px 0 0;color:{INK};"
            f"font-size:{size}px;line-height:1.2'>{esc(story['title'])}</a>"
            f"<p class='body'>{summary(story, limit)}</p>"
            f"<p style='margin:8px 0 0'><a href='{url}' class='more' style='color:{INK}'>Read on →</a></p>"
        )

    def split_section(items):
        """~IMAGE_SHARE of the section gets pictures (best-ranked stories that
        actually have a photo); everything else is shown text-only."""
        want = int(len(items) * IMAGE_SHARE + 0.5)
        with_photo = [s for s in items if s["id"] in images][:want]
        chosen = {s["id"] for s in with_photo}
        return with_photo, [s for s in items if s["id"] not in chosen]

    def add_rows(group, with_photo):
        soft = f"border-bottom:1px solid {RULE_SOFT}"
        for i in range(0, len(group), 2):
            pair = group[i:i + 2]
            if len(pair) == 2:
                p.append(
                    "<tr><td class='pad' style='padding:0 32px'>"
                    "<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
                    f"<td class='col coll' width='50%' valign='top' style='padding:16px 14px 16px 0;{soft}'>"
                    f"{cell(pair[0], with_photo=with_photo)}</td>"
                    f"<td class='col colr' width='50%' valign='top' "
                    f"style='padding:16px 0 16px 14px;border-left:1px solid {INK};{soft}'>"
                    f"{cell(pair[1], with_photo=with_photo)}</td>"
                    "</tr></table></td></tr>"
                )
            else:  # odd one out gets the full column width
                p.append(
                    f"<tr><td class='pad' style='padding:16px 32px;{soft}'>"
                    f"{cell(pair[0], wide=True, with_photo=with_photo)}</td></tr>"
                )

    for category, items in sections.items():
        p.append(
            "<tr><td class='pad' style='padding:24px 32px 0'>"
            f"<div class='sec' style='border-top:3px double {INK};border-bottom:1px solid {INK};"
            f"padding:7px 0'>{esc(category)}</div></td></tr>"
        )
        with_photo, text_only = split_section(items)
        add_rows(with_photo, True)    # picture rows first...
        add_rows(text_only, False)    # ...then the text-only rows

    # ---- Footer ----
    p.append(
        f"<tr><td class='pad' style='padding:26px 32px 22px'>"
        f"<div style='border-top:3px double {INK};padding-top:12px;text-align:center'>"
        f"<p class='body' style='text-align:center;font-style:italic;font-size:12px;margin:0'>"
        "Stories are ranked by keyword rules, not verified severity, and photos belong to "
        "their publishers. Read the original sources before acting on any claim.</p>"
        f"<p class='meta' style='margin:8px 0 0;text-align:center'>"
        f"Printed {now.strftime('%d %b %Y, %H:%M')} IST</p>"
        "</div></td></tr>"
    )

    p.append("</table></td></tr></table></body></html>")
    return "".join(p), inline


def make_plain_text(stories):
    lines = ["THE TECH BITES DAILY", datetime.now(IST).strftime("%A, %d %B %Y"), ""]
    for story in stories:
        lines.append(f"[{story['category']}] {story['title']}")
        lines.append(f"  {story['source']} - {story['url']}")
        lines.append("")
    return "\n".join(lines)


def build_message(stories, images, sender, recipient):
    message = EmailMessage()
    message["Subject"] = f"Daily Tech Briefing — {datetime.now(IST).strftime('%d %b %Y')}"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(make_plain_text(stories))

    html_body, inline = make_email(stories, images)
    message.add_alternative(html_body, subtype="html")

    html_part = message.get_payload()[1]
    for cid, data, subtype, filename in inline:
        html_part.add_related(data, "image", subtype, cid=cid, filename=filename)

    return message


def send_email(stories, images):
    sender = os.environ["EMAIL_ADDRESS"]
    password = os.environ["EMAIL_APP_PASSWORD"]
    recipient = os.environ.get("EMAIL_TO", sender)

    message = build_message(stories, images, sender, recipient)

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(sender, password)
        server.send_message(message)

    logging.info("Email sent to %s", recipient)


if __name__ == "__main__":
    stories = collect_stories()
    logging.info("Selected %d stories for the digest", len(stories))
    images = fetch_images(stories)
    send_email(stories, images)