
import os
import re
import html
import smtplib
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import feedparser

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Official sources and established technical news sources.
FEEDS = [
    {
        "name": "GitHub Blog",
        "url": "https://github.blog/feed/",
        "category": "Open Source",
        "base_score": 2,
    },
    {
        "name": "GitHub Security Advisories",
        "url": "https://github.com/advisories.atom",
        "category": "Security",
        "base_score": 4,
    },
    {
        "name": "Cloudflare Blog",
        "url": "https://blog.cloudflare.com/rss/",
        "category": "Infrastructure",
        "base_score": 2,
    },
    {
        "name": "BleepingComputer",
        "url": "https://www.bleepingcomputer.com/feed/",
        "category": "Security News",
        "base_score": 2,
    },
]

# Higher scores mean greater priority. These rules are only heuristics.
KEYWORDS = {
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
    "release": 2,
    "open source": 1,
    "artificial intelligence": 2,
    "machine learning": 2,
    "large language model": 2,
    "llm": 1,
    "reverse engineering": 3,
    "developer tools": 1,
}

MAX_STORIES = 25
LOOKBACK_HOURS = 48


def clean_text(value):
    """Remove HTML tags and normalize whitespace."""
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_date(entry):
    """Return a timezone-aware publication timestamp when available."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")

    if parsed:
        import calendar
        return datetime.fromtimestamp(
            calendar.timegm(parsed), tz=timezone.utc
        )

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
                    "title": title,
                    "url": url,
                    "source": feed["name"],
                    "category": feed["category"],
                    "description": description,
                    "published": published,
                    "score": score,
                    "matched": matched,
                })

        except Exception:
            logging.exception("Failed to fetch %s", feed["name"])

    # Prioritize relevance, then the newest publication date.
    stories.sort(
        key=lambda s: (
            s["score"],
            s["published"] or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )

    return stories[:MAX_STORIES]


def make_email(stories):
    today = datetime.now().strftime("%d %b %Y")
    sections = {}

    for story in stories:
        category = story["category"]
        sections.setdefault(category, []).append(story)

    parts = [
        "<html><body style='font-family:Arial,sans-serif;"
        "max-width:760px;margin:auto;color:#222'>",
        f"<h1>Daily Technology Briefing — {today}</h1>",
        "<p>Rule-based digest from selected technology feeds. "
        "Read original sources before acting on a claim.</p>",
    ]

    if not stories:
        parts.append(
            "<p>No recent stories were collected. This may mean "
            "there were no new items or a feed was unavailable.</p>"
        )

    for category, items in sections.items():
        parts.append(f"<h2>{html.escape(category)}</h2>")

        for item in items:
            title = html.escape(item["title"])
            url = html.escape(item["url"], quote=True)
            source = html.escape(item["source"])
            score = item["score"]
            date = (
                item["published"].strftime("%d %b %Y %H:%M UTC")
                if item["published"]
                else "Publication date unavailable"
            )

            parts.append("<div style='margin-bottom:20px'>")
            parts.append(
                f"<p><strong><a href='{url}'>{title}</a></strong></p>"
            )
            parts.append(
                f"<p><small>{source} · {date} · "
                f"Priority score: {score}</small></p>"
            )

            if item["description"]:
                description = html.escape(item["description"])
                parts.append(f"<p>{description}</p>")
            else:
                parts.append(
                    "<p>Open the original link for the full details.</p>"
                )

            if item["matched"]:
                keywords = ", ".join(item["matched"][:5])
                parts.append(
                    f"<p><small>Matched topics: "
                    f"{html.escape(keywords)}</small></p>"
                )

            parts.append("</div><hr>")

    parts.append(
        "<p><small>Priority scores are keyword-based, not verified "
        "severity ratings. This digest may miss important stories.</small></p>"
    )
    parts.append("</body></html>")
    return "".join(parts)


def send_email(stories):
    sender = os.environ["EMAIL_ADDRESS"]
    password = os.environ["EMAIL_APP_PASSWORD"]
    recipient = os.environ.get("EMAIL_TO", sender)

    message = EmailMessage()
    message["Subject"] = (
        f"Daily Tech Briefing — {datetime.now().strftime('%d %b %Y')}"
    )
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        f"Daily Technology Briefing\n\n"
        f"{len(stories)} stories collected.\n"
        "Open this email in an HTML-capable mail client to read the digest."
    )
    message.add_alternative(make_email(stories), subtype="html")

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(sender, password)
        server.send_message(message)

    logging.info("Email sent to %s", recipient)


if __name__ == "__main__":
    stories = collect_stories()
    logging.info("Selected %d stories for the digest", len(stories))
    send_email(stories)
