import os
import re
import io
import json
import html
import smtplib
import hashlib
import time
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
MASTHEAD_MOBILE_URL = "https://cdn.jsdelivr.net/gh/mohamed-ashif-m/tech-bites@main/assets/masthead-mobile.png"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IST = timezone(timedelta(hours=5, minutes=30))
FIRST_ISSUE = date(2026, 10, 24)  # used for the "No. N" issue counter

# Official sources and established technical news sources.
FEEDS = [
    # ---- Open source / dev platforms ----
    {"name": "GitHub Blog", "url": "https://github.blog/feed/",
     "category": "Open Source", "base_score": 2},

    # ---- Security ----
    # These two have no working RSS feed, so they are read from their JSON data instead.
    {"name": "GitHub Security Advisories", "format": "github_advisories",
     "url": "https://api.github.com/advisories?type=reviewed&severity=critical&per_page=20",
     "category": "Security", "base_score": 4},
    {"name": "CISA Known Exploited", "format": "cisa_kev",
     "url": ["https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
             "https://raw.githubusercontent.com/cisagov/kev-data/develop/known_exploited_vulnerabilities.json"],
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

    # ---- Industry & business news (company moves, layoffs, deals) ----
    # Feed URLs below are from memory: check the log for "Could not read feed".
    {"name": "CNBC Technology", "url": "https://www.cnbc.com/id/19854910/device/rss/rss.html",
     "category": "Business & Industry", "base_score": 3},
    {"name": "GeekWire", "url": "https://www.geekwire.com/feed/",
     "category": "Business & Industry", "base_score": 2},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml",
     "category": "Business & Industry", "base_score": 2},
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/",
     "category": "Business & Industry", "base_score": 2},
    {"name": "Economic Times Tech", "url": "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms",
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
    # Company moves and business news
    "layoff": 5,
    "job cuts": 5,
    "buyout": 5,
    "voluntary exit": 5,
    "voluntary retirement": 5,
    "workforce": 2,
    "acquisition": 3,
    "acquires": 3,
    "merger": 3,
    "antitrust": 3,
    "earnings": 2,
    "billion": 2,
}

MAX_STORIES = 30
MAX_PER_CATEGORY = 3
LOOKBACK_HOURS = 24

# ---- Cross-source boost: the same event reported by several outlets ranks higher ----
CROSS_SOURCE_BONUS = 4      # points for each additional outlet covering the same story
CROSS_SOURCE_BONUS_MAX = 12

# ---- Article photos ----
SMTP_TIMEOUT = 120        # seconds per network operation (photos make the email large)
SMTP_ATTEMPTS = 3
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
# Overall email width in px. 600-700 is the traditional safe size; 800 reads
# better on desktop and still shrinks to fit phones and narrow preview panes.
EMAIL_WIDTH = 800
CONTENT_WIDTH = EMAIL_WIDTH - 64                  # inside the 32px side padding
GRID_PHOTO_WIDTH = (CONTENT_WIDTH - 30) // 2      # one column of the 2-column grid
LEAD_PHOTO_WIDTH = CONTENT_WIDTH - 70             # inside the dark top-story box

RED_INK = "#8B1A1A"
DARK = "#2B1D13"        # inverted "contrast box" used for the top story
DARK_TEXT = "#E6D9BC"
DARK_MUTED = "#CDBB95"
DARK_RED = "#F0A58F"


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


_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "its", "his", "her",
    "has", "have", "had", "new", "says", "say", "said", "how", "why", "what", "will", "into",
    "over", "after", "more", "than", "can", "could", "may", "amid", "about", "just", "now",
    "not", "but", "you", "your", "all", "one", "out", "get", "gets", "use", "uses", "using",
    "via", "per", "off", "too", "their", "they", "them", "been", "being", "also", "who",
    "when", "where", "which", "while", "would", "should", "report", "reports", "update",
    "updates", "security", "launches", "launch", "released", "releases", "release",
}


def _title_tokens(title):
    text = re.sub(r"(?<=\d),(?=\d)", "", title.lower())  # 60,000 -> 60000
    return {t for t in re.findall(r"[a-z0-9]+", text) if len(t) > 2 and t not in _STOPWORDS}


def _same_story(tokens, cluster_tokens):
    shared = len(tokens & cluster_tokens)
    if shared < 3:
        return False
    overlap = shared / max(1, min(len(tokens), len(cluster_tokens)))
    return overlap >= 0.5 or (shared >= 4 and overlap >= 0.4) or (shared >= 5 and overlap >= 0.3)


def merge_related(stories):
    """Combine headlines about the same event from different outlets.

    The best-scoring version represents the event, gains CROSS_SOURCE_BONUS
    points per extra outlet (so big stories rise), and remembers who else
    covered it in story["also"].
    """
    clusters = []  # each: {"tokens": set, "members": [stories]}
    for story in sorted(stories, key=_sort_key, reverse=True):
        tokens = _title_tokens(story["title"])
        for cluster in clusters:
            if _same_story(tokens, cluster["tokens"]):
                cluster["members"].append(story)
                cluster["tokens"] |= tokens
                break
        else:
            clusters.append({"tokens": set(tokens), "members": [story]})

    # Different outlets word the same event differently, so join clusters that
    # have grown similar enough until nothing more merges.
    changed = True
    while changed:
        changed = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                if _same_story(clusters[i]["tokens"], clusters[j]["tokens"]):
                    clusters[i]["members"] += clusters[j]["members"]
                    clusters[i]["tokens"] |= clusters[j]["tokens"]
                    del clusters[j]
                    changed = True
                    break
            if changed:
                break

    merged = []
    for cluster in clusters:
        cluster["members"].sort(key=_sort_key, reverse=True)
        best, rest = cluster["members"][0], cluster["members"][1:]
        others = []
        for member in rest:
            if member["source"] != best["source"] and member["source"] not in others:
                others.append(member["source"])
            if not best.get("image_url") and member.get("image_url"):
                best["image_url"] = member["image_url"]
        if others:
            best["score"] += min(CROSS_SOURCE_BONUS * len(others), CROSS_SOURCE_BONUS_MAX)
            best["also"] = others
            best["matched"] = best["matched"] + [f"covered by {len(others) + 1} outlets"]
        merged.append(best)
    return merged


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


def _struct(dt):
    return dt.astimezone(timezone.utc).timetuple()


def _json_get(urls):
    """Fetch JSON from the first URL that works (a list of mirrors is allowed)."""
    last = None
    for url in ([urls] if isinstance(urls, str) else urls):
        for attempt in range(2):
            try:
                _, data = _http_get(url, timeout=20, limit=5_000_000)
                return json.loads(data.decode("utf-8", "ignore"))
            except Exception as exc:
                last = exc
                time.sleep(2)
    raise last


def github_advisory_entries(feed, cutoff):
    """Newest critical, reviewed GitHub security advisories."""
    out = []
    for item in _json_get(feed["url"]):
        stamp = item.get("published_at") or ""
        if not stamp:
            continue
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if when < cutoff:
            continue
        ident = item.get("cve_id") or item.get("ghsa_id") or ""
        out.append({
            "title": f"Critical flaw: {item.get('summary', 'Security advisory')} ({ident})",
            "link": item.get("html_url", ""),
            "summary": f"Critical security advisory. {item.get('description', '')}"[:700],
            "published_parsed": _struct(when),
        })
    return out[:5]


def cisa_kev_entries(feed, cutoff):
    """Vulnerabilities CISA recently added to its Known Exploited list."""
    now = datetime.now(timezone.utc)
    out = []
    for item in _json_get(feed["url"]).get("vulnerabilities", []):
        try:
            day = datetime.fromisoformat(item["dateAdded"]).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        when = min(now, day + timedelta(hours=23, minutes=59))  # only the date is published
        if when < cutoff:
            continue
        cve = item.get("cveID", "")
        name = f"{item.get('vendorProject', '')} {item.get('product', '')}".strip()
        out.append({
            "title": f"CISA warns of actively exploited {name} flaw ({cve})",
            "link": f"https://nvd.nist.gov/vuln/detail/{cve}",
            "summary": f"Known exploited vulnerability. {item.get('shortDescription', '')} "
                       f"Required action: {item.get('requiredAction', '')}"[:700],
            "published_parsed": _struct(when),
        })
    out.sort(key=lambda e: e["published_parsed"], reverse=True)
    return out[:5]


def parse_feed_with_retry(url, attempts=3):
    """feedparser.parse, retried: home connections drop requests now and then."""
    parsed = None
    for attempt in range(1, attempts + 1):
        try:
            parsed = feedparser.parse(url, request_headers={"User-Agent": "DailyTechBriefing/1.0"})
            if parsed.entries or not parsed.bozo:
                return parsed
        except Exception as exc:
            logging.info("Feed request failed (%s), attempt %d/%d", exc, attempt, attempts)
        if attempt < attempts:
            time.sleep(2 * attempt)
    if parsed is None:
        raise ConnectionError(f"could not reach {url}")
    return parsed


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


def page_blurb(page_url):
    """Short description of an article page (og:description / meta description)."""
    try:
        _, data = _http_get(page_url, timeout=8, limit=300_000)
        text = data.decode("utf-8", "ignore")
        patterns = (
            r"<meta[^>]+property=[\"']og:description[\"'][^>]+content=[\"']([^\"']+)",
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:description",
            r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']+)",
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+name=[\"']description",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                blurb = clean_text(html.unescape(match.group(1)))
                if len(blurb) >= 40:
                    return blurb
    except Exception as exc:
        logging.info("Description lookup failed for %s (%s)", page_url, exc)
    return ""


def add_community_blurbs(stories):
    """Hacker News items carry no text, so borrow each article's own summary."""
    targets = [s for s in stories if s.get("community")]
    if not targets:
        return
    with ThreadPoolExecutor(max_workers=8) as pool:
        for story, blurb in zip(targets, pool.map(lambda s: page_blurb(s["url"]), targets)):
            story["description"] = blurb


def download_image(url):
    """Return raw image bytes if the URL is a usable, large-enough photo."""
    try:
        try:
            content_type, data = _http_get(url, timeout=12, limit=MAX_DOWNLOAD_BYTES)
        except Exception:  # one quick retry for dropped connections / TLS timeouts
            time.sleep(1.5)
            content_type, data = _http_get(url, timeout=12, limit=MAX_DOWNLOAD_BYTES)
        if not content_type.lower().startswith("image/") or "svg" in content_type.lower():
            return None
        with Image.open(io.BytesIO(data)) as probe:
            if probe.width < MIN_SRC_WIDTH or probe.height < MIN_SRC_HEIGHT:
                return None
        return data
    except Exception as exc:
        logging.info("Image download failed for %s (%s)", url, exc)
        return None


def parse_community(raw_summary, entry):
    """Pull points, comment count and discussion link out of a hnrss item."""
    text = html.unescape(raw_summary or "")
    points = re.search(r"Points:\s*(\d+)", text)
    comments = re.search(r"#\s*Comments:\s*(\d+)", text)
    discuss = (entry.get("comments") or "").strip()
    if not discuss:
        found = re.search(r"https://news\.ycombinator\.com/item\?id=\d+", text)
        discuss = found.group(0) if found else ""
    domain = urllib.parse.urlparse(entry.get("link", "")).netloc.removeprefix("www.")
    return {
        "points": int(points.group(1)) if points else 0,
        "comments": int(comments.group(1)) if comments else 0,
        "discuss": discuss,
        "domain": domain,
    }


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

    candidates = [s for s in stories if not s.get("community")][:IMAGE_CANDIDATES]
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
        image.save(out, "JPEG", quality=60, optimize=True, progressive=True)
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
            kind = feed.get("format")
            if kind == "github_advisories":
                entries = github_advisory_entries(feed, cutoff)
            elif kind == "cisa_kev":
                entries = cisa_kev_entries(feed, cutoff)
            else:
                parsed_feed = parse_feed_with_retry(feed["url"])
                if parsed_feed.bozo and not parsed_feed.entries:
                    logging.warning("Could not read feed: %s", feed["name"])
                    continue
                entries = parsed_feed.entries

            logging.info("%s: fetched %d entries", feed["name"], len(entries))

            for entry in entries:
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

                raw_summary = entry.get("summary", "") or entry.get("description", "")
                description = clean_text(raw_summary)[:700]
                community = parse_community(raw_summary, entry) if feed["category"] == "Community" else None
                if community:
                    description = ""  # the feed text is only links and counters

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
                    "image_url": None if community else image_from_entry(entry),
                    "community": community,
                })

        except Exception:
            logging.exception("Failed to fetch %s", feed["name"])

    chosen = select_stories(merge_related(stories))
    add_community_blurbs(chosen)
    return chosen


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
  .desk{{display:none!important}}
  .mob{{display:block!important;max-height:none!important;overflow:visible!important}}
  .leadbox{{padding:6px!important}}
  .leadin{{padding:14px!important}}
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
    for story in [s for s in stories if s["category"] != "Community"][:6]:
        if story["id"] in images:
            lead = story
            break
    if lead is None and stories:
        lead = next((s for s in stories if s["category"] != "Community"), stories[0])

    sections = {}
    for story in stories:
        if story is not lead:
            sections.setdefault(story["category"], []).append(story)

    def cid_for(data, subtype, filename):
        cid = make_msgid(domain="techbites.local")
        inline.append((cid, data, subtype, filename))
        return cid[1:-1]

    def photo(story, size, shown_width, border=INK):
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
            f"height:auto;border:1px solid {border};margin:0 0 8px'>"
        )

    def kicker(story, with_category=False, dark=False):
        parts = [esc(story["source"]), _fmt_dt(story["published"])]
        if with_category:
            parts.insert(1, esc(story["category"]))
        text = " · ".join(parts)
        if story["score"] / max_score >= 0.66:
            text = f"<span style='color:{DARK_RED if dark else RED_INK}'>● Priority</span> · " + text
        colour = f" style='color:{DARK_MUTED}'" if dark else ""
        return f"<div class='kick'{colour}>{text}</div>"

    def also_line(story, dark=False):
        others = story.get("also") or []
        if not others:
            return ""
        names = ", ".join(esc(n) for n in others[:3])
        extra = f" +{len(others) - 3} more" if len(others) > 3 else ""
        colour = "#F0C987" if dark else "#5A4A33"
        return (
    f"<div style=\"font-family:{BODY_FONT};font-size:13px;font-style:italic;"
    f"font-weight:bold;color:{colour};padding-top:6px;\">"
    f"<span style=\"color:{colour}\">Also reported by {names}{extra}</span></div>"
)

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
        f"<table role='presentation' class='wrap' width='{EMAIL_WIDTH}' cellpadding='0' cellspacing='0' "
        f"style='width:100%;max-width:{EMAIL_WIDTH}px;background:{PAPER};border:1px solid {INK}'>"
    )

    # ---- Masthead: wide version for desktop, compact version for phones ----
    masthead = (
        f"<img src='{html.escape(MASTHEAD_URL, quote=True)}' width='{CONTENT_WIDTH}' alt='The Tech Bites Daily' "
        f"class='fluid desk' "
        f"style='display:block;width:100%;max-width:{CONTENT_WIDTH}px;height:auto;border:0;margin:0 auto'>"
        # hidden by default; the phone stylesheet swaps it in (clients without
        # media-query support simply keep the desktop version)
        f"<img src='{html.escape(MASTHEAD_MOBILE_URL, quote=True)}' width='340' alt='The Tech Bites Daily' "
        f"class='fluid mob' "
        f"style='display:none;max-height:0;overflow:hidden;width:100%;max-width:340px;height:auto;border:0;margin:0 auto'>"
    )
    p.append(f"<tr><td class='pad' style='padding:18px 32px 0'>{masthead}</td></tr>")

    # ---- Dateline: three columns on desktop, two centred lines on phones ----
    p.append(
        f"<tr><td class='pad' style='padding:6px 32px 0'>"
        f"<table role='presentation' class='desk' width='100%' cellpadding='0' cellspacing='0' "
        f"style='border-bottom:1px solid {INK};table-layout:fixed'><tr>"
        f"<td class='meta' width='33%' align='left' style='padding:4px 0'>{esc(today)}</td>"
        f"<td class='meta' width='33%' align='center' style='padding:4px 0'>Vol. I · No. {issue_no}</td>"
        f"<td class='meta' width='33%' align='right' style='padding:4px 0'>{len(stories)} dispatches</td>"
        "</tr></table>"
        f"<div class='mob meta' style='display:none;max-height:0;overflow:hidden;text-align:center;"
        f"padding:7px 0 8px;border-bottom:1px solid {INK}'>"
        f"<div style='font-size:11px;letter-spacing:1.2px'>{esc(today)}</div>"
        f"<div style='font-size:9.5px;letter-spacing:1.4px;margin-top:4px;color:#6B5E48'>"
        f"Vol. I · No. {issue_no} · {len(stories)} dispatches</div></div>"
        "</td></tr>"
    )

    if not stories:
        p.append(
            f"<tr><td class='pad' style='padding:40px 32px;text-align:center'>"
            f"<div class='head' style='font-size:22px'>No dispatches today</div>"
            f"<p class='body' style='text-align:center'>Either there was no new coverage "
            "or a feed was unavailable.</p></td></tr>"
        )

    # ---- Lead story: inverted (dark) box so the main highlight stands out ----
    if lead:
        url = esc(lead["url"], quote=True)
        p.append(
            "<tr><td class='pad' style='padding:18px 32px 6px'>"
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='background:{DARK};border:1px solid {INK}'><tr>"
            f"<td class='leadbox' style='padding:10px'>"
            f"<div class='leadin' style='border:3px double #7C6948;padding:20px 22px'>"
            f"<div class='kick' style='text-align:center;color:{DARK_MUTED};letter-spacing:5px;"
            f"padding-bottom:10px'>★ Top Story ★</div>"
            f"{kicker(lead, with_category=True, dark=True)}"
            f"<a href='{url}' class='head lead' style='display:block;margin:6px 0 14px;color:{PAPER};"
            f"font-size:32px;line-height:1.12'>{esc(lead['title'])}</a>"
            f"{photo(lead, (900, 450), LEAD_PHOTO_WIDTH, border=DARK_MUTED)}"
            f"<p class='body' style='font-size:15px;color:{DARK_TEXT}'>{summary(lead, 420)}</p>"
            f"{also_line(lead, dark=True)}"
            f"<p style='margin:12px 0 0'><a href='{url}' class='more' "
            f"style='color:{PAPER}'>Continue reading →</a></p>"
            "</div></td></tr></table></td></tr>"
        )

    # ---- Sections ----
    def cell(story, wide=False, with_photo=True):
        url = esc(story["url"], quote=True)
        img = ""
        if with_photo:
            img = (photo(story, (900, 378), CONTENT_WIDTH) if wide
               else photo(story, (480, 320), GRID_PHOTO_WIDTH))
        size = 22 if wide else 18
        # text-only stories have more room, so they can carry a longer summary
        limit = (300 if wide else 220) if not with_photo else (260 if wide else 170)
        return (
            f"{img}{kicker(story)}"
            f"<a href='{url}' class='head' style='display:block;margin:5px 0 0;color:{INK};"
            f"font-size:{size}px;line-height:1.2'>{esc(story['title'])}</a>"
            f"<p class='body'>{summary(story, limit)}</p>"
            f"{also_line(story)}"
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

    def add_community(items):
        """'Talk of the Town': a ranked board with a vote stamp per story."""
        items = sorted(items, key=lambda x: -(x["community"] or {}).get("points", 0))
        p.append(
            "<tr><td class='pad' style='padding:24px 32px 0'>"
            f"<div class='sec' style='border-top:3px double {INK};border-bottom:1px solid {INK};"
            f"padding:7px 0 3px'>Talk of the Town</div>"
            f"<div style=\"font-family:{BODY_FONT};font-size:12px;font-style:italic;text-align:center;"
            f"color:#5A4A33;padding:5px 0 7px;border-bottom:1px solid {INK}\">"
            "What the developer crowd is voting up and arguing over on Hacker News</div></td></tr>"
        )
        for rank, story in enumerate(items, 1):
            c = story["community"] or {}
            url = esc(story["url"], quote=True)
            discuss = esc(c.get("discuss") or story["url"], quote=True)
            hot = c.get("points", 0) >= 500
            if story["description"]:
                blurb = f"<p class='body' style='margin:6px 0 0'>{esc(_truncate(story['description'], 200))}</p>"
            else:
                blurb = ""
            stamp_bg, stamp_fg = (DARK, PAPER) if hot else ("transparent", INK)
            fire = "<div style=\"font-size:9px;letter-spacing:1.5px;color:#F0C987;margin-top:2px\">HOT</div>" if hot else ""
            p.append(
                "<tr><td class='pad' style='padding:0 32px'>"
                f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
                f"style='border-bottom:1px solid {RULE_SOFT}'><tr>"
                f"<td width='64' valign='top' style='padding:14px 0'>"
                f"<div style=\"width:56px;text-align:center;padding:7px 0;border:2px solid {INK};"
                f"background:{stamp_bg};color:{stamp_fg};font-family:{HEAD_FONT};\">"
                f"<div style=\"font-size:22px;font-weight:900;line-height:1\">{c.get('points', 0)}</div>"
                f"<div style=\"font-family:{BODY_FONT};font-size:9px;letter-spacing:1.5px;"
                f"text-transform:uppercase;margin-top:3px\">points</div>{fire}</div></td>"
                f"<td valign='top' style='padding:14px 0 14px 14px'>"
                f"<div class='kick'>No. {rank} · {esc(c.get('domain') or story['source'])} · "
                f"{_fmt_dt(story['published'])}</div>"
                f"<a href='{url}' class='head' style='display:block;margin:4px 0 0;color:{INK};"
                f"font-size:18px;line-height:1.2'>{esc(story['title'])}</a>"
                f"{blurb}"
                f"<div style=\"font-family:{BODY_FONT};font-size:12.5px;margin-top:7px;color:#2B251D\">"
                f"<b>{c.get('comments', 0)}</b> comments &nbsp;·&nbsp; "
                f"<a href='{discuss}' class='more' style='color:{INK}'>Join the discussion →</a>"
                f" &nbsp;·&nbsp; <a href='{url}' class='more' style='color:{INK}'>Read the article →</a>"
                "</div></td></tr></table></td></tr>"
            )

    for category, items in sections.items():
        if category == "Community":
            add_community(items)
            continue
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

    size_kb = len(message.as_bytes()) // 1024
    logging.info("Sending email (%d KB)...", size_kb)

    # The message carries the photos, so give a slow connection plenty of time
    # and retry a couple of times if the upload is cut off.
    for attempt in range(1, SMTP_ATTEMPTS + 1):
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=SMTP_TIMEOUT) as server:
                server.starttls()
                server.login(sender, password)
                server.send_message(message)
            break
        except (OSError, smtplib.SMTPServerDisconnected) as exc:
            logging.warning("Send attempt %d/%d failed: %s", attempt, SMTP_ATTEMPTS, exc)
            if attempt == SMTP_ATTEMPTS:
                raise
            time.sleep(5 * attempt)

    logging.info("Email sent to %s", recipient)


if __name__ == "__main__":
    stories = collect_stories()
    logging.info("Selected %d stories for the digest", len(stories))
    images = fetch_images(stories)
    send_email(stories, images)