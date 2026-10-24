
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

LOGO_URL = "https://cdn.jsdelivr.net/gh/mohamed-ashif-m/tech-bites@main/assets/logo.webp"

ACCENTS = ["#4F46E5", "#0EA5E9", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6"]


def _truncate(text, limit=220):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"

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


def make_email(stories, logo_url=None):
    today = datetime.now().strftime("%A, %d %B %Y")
    sections = {}
    for story in stories:
        sections.setdefault(story["category"], []).append(story)

    max_score = max((s["score"] for s in stories), default=0) or 1

    if logo_url:
        logo_html = (
            f"<img src='{html.escape(logo_url, quote=True)}' alt='Logo' height='44' "
            "style='display:block;height:44px;width:auto;border:0'>"
        )
    else:
        logo_html = (
            "<span style='font-size:20px;font-weight:700;color:#ffffff'>"
            "Tech Briefing</span>"
        )

    p = []
    p.append(
        "<!DOCTYPE html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='color-scheme' content='light'>"
        "<title>Daily Technology Briefing</title></head>"
        "<body style='margin:0;padding:0;background:#F1F5F9;"
        "font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,"
        "Arial,sans-serif;color:#1E293B'>"
    )

    # Hidden preheader (inbox preview text)
    p.append(
        "<div style='display:none;max-height:0;overflow:hidden;opacity:0'>"
        f"{len(stories)} stories across {len(sections)} topics for {today}"
        "</div>"
    )

    p.append(
        "<table role='presentation' width='100%' cellpadding='0' "
        "cellspacing='0' style='background:#F1F5F9'><tr>"
        "<td align='center' style='padding:24px 12px'>"
        "<table role='presentation' width='640' cellpadding='0' "
        "cellspacing='0' style='width:100%;max-width:640px'>"
    )

    # ---- Header ----
    p.append(
        "<tr><td style='background:#0F172A;border-radius:16px 16px 0 0;"
        "padding:28px 32px'>"
        "<table role='presentation' width='100%' cellpadding='0' "
        "cellspacing='0'><tr>"
        f"<td align='left' valign='middle'>{logo_html}</td>"
        "<td align='right' valign='middle' style='color:#94A3B8;"
        f"font-size:12px;letter-spacing:.5px'>{html.escape(today)}</td>"
        "</tr></table>"
        "<h1 style='margin:24px 0 6px;font-size:26px;line-height:1.25;"
        "color:#ffffff;font-weight:700'>Daily Technology Briefing</h1>"
        f"<p style='margin:0;font-size:14px;color:#CBD5E1'>"
        f"{len(stories)} stories · {len(sections)} topics</p>"
        "</td></tr>"
        # accent strip
        "<tr><td style='height:4px;background:#4F46E5;font-size:0;"
        "line-height:0'>&nbsp;</td></tr>"
    )

    # ---- Body ----
    p.append(
        "<tr><td style='background:#ffffff;padding:28px 32px 8px'>"
        "<p style='margin:0 0 8px;font-size:14px;line-height:1.6;"
        "color:#475569'>Here is your rule-based digest from selected "
        "technology feeds. Read the original sources before acting on "
        "any claim.</p></td></tr>"
    )

    if not stories:
        p.append(
            "<tr><td style='background:#ffffff;padding:16px 32px 32px'>"
            "<div style='background:#FEF3C7;border-left:4px solid #F59E0B;"
            "padding:14px 16px;border-radius:6px;font-size:14px;"
            "color:#92400E'>No recent stories were collected. This may "
            "mean there were no new items or a feed was unavailable."
            "</div></td></tr>"
        )

    for idx, (category, items) in enumerate(sections.items()):
        accent = ACCENTS[idx % len(ACCENTS)]

        p.append(
            "<tr><td style='background:#ffffff;padding:20px 32px 4px'>"
            "<table role='presentation' width='100%' cellpadding='0' "
            "cellspacing='0'><tr>"
            f"<td style='border-bottom:2px solid {accent};padding-bottom:8px'>"
            f"<span style='display:inline-block;background:{accent};"
            "color:#ffffff;font-size:11px;font-weight:700;"
            "letter-spacing:1px;text-transform:uppercase;padding:5px 12px;"
            f"border-radius:999px'>{html.escape(category)}</span>"
            f"<span style='color:#94A3B8;font-size:12px;margin-left:10px'>"
            f"{len(items)} {'story' if len(items) == 1 else 'stories'}</span>"
            "</td></tr></table></td></tr>"
        )

        for item in items:
            title = html.escape(item["title"])
            url = html.escape(item["url"], quote=True)
            source = html.escape(item["source"])
            score = item["score"]
            date = (
                item["published"].strftime("%d %b %Y, %H:%M UTC")
                if item["published"]
                else "Date unavailable"
            )

            desc = (
                html.escape(_truncate(item["description"]))
                if item["description"]
                else "Open the original link for the full details."
            )

            ratio = score / max_score
            if ratio >= 0.66:
                pill_bg, pill_fg = "#DCFCE7", "#166534"
            elif ratio >= 0.33:
                pill_bg, pill_fg = "#FEF9C3", "#854D0E"
            else:
                pill_bg, pill_fg = "#E2E8F0", "#475569"

            tags = ""
            if item["matched"]:
                tags = "".join(
                    "<span style='display:inline-block;background:#F1F5F9;"
                    "color:#475569;font-size:11px;padding:3px 9px;"
                    "border-radius:6px;margin:6px 6px 0 0'>"
                    f"{html.escape(k)}</span>"
                    for k in item["matched"][:5]
                )

            p.append(
                "<tr><td style='background:#ffffff;padding:14px 32px 0'>"
                "<table role='presentation' width='100%' cellpadding='0' "
                "cellspacing='0' style='border:1px solid #E2E8F0;"
                f"border-left:4px solid {accent};border-radius:10px'>"
                "<tr><td style='padding:16px 18px'>"
                "<table role='presentation' width='100%' cellpadding='0' "
                "cellspacing='0'><tr>"
                "<td style='font-size:12px;color:#64748B'>"
                f"<strong style='color:#334155'>{source}</strong>"
                f" · {date}</td>"
                "<td align='right'>"
                f"<span style='background:{pill_bg};color:{pill_fg};"
                "font-size:11px;font-weight:700;padding:3px 9px;"
                f"border-radius:999px;white-space:nowrap'>Score {score}</span>"
                "</td></tr></table>"
                f"<a href='{url}' style='display:block;margin-top:10px;"
                "font-size:17px;line-height:1.35;font-weight:700;"
                f"color:#0F172A;text-decoration:none'>{title}</a>"
                f"<p style='margin:8px 0 0;font-size:14px;line-height:1.6;"
                f"color:#475569'>{desc}</p>"
                f"<div>{tags}</div>"
                f"<a href='{url}' style='display:inline-block;margin-top:14px;"
                f"background:{accent};color:#ffffff;font-size:13px;"
                "font-weight:600;text-decoration:none;padding:8px 16px;"
                "border-radius:8px'>Read article →</a>"
                "</td></tr></table></td></tr>"
            )

    # ---- Footer ----
    p.append(
        "<tr><td style='background:#ffffff;padding:24px 32px'></td></tr>"
        "<tr><td style='background:#0F172A;border-radius:0 0 16px 16px;"
        "padding:22px 32px;text-align:center'>"
        "<p style='margin:0 0 6px;font-size:12px;line-height:1.6;"
        "color:#94A3B8'>Priority scores are keyword-based, not verified "
        "severity ratings. This digest may miss important stories.</p>"
        f"<p style='margin:0;font-size:11px;color:#64748B'>"
        f"Generated {datetime.now().strftime('%d %b %Y, %H:%M')}</p>"
        "</td></tr>"
    )

    p.append("</table></td></tr></table></body></html>")
    return "".join(p)


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
        "Daily Technology Briefing\n\n"
        f"{len(stories)} stories collected.\n"
        "Open this email in an HTML-capable mail client to read the digest."
    )

    message.add_alternative(make_email(stories, LOGO_URL), subtype="html")

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(sender, password)
        server.send_message(message)

    logging.info("Email sent to %s", recipient)


if __name__ == "__main__":
    stories = collect_stories()
    logging.info("Selected %d stories for the digest", len(stories))
    send_email(stories)
