<p align="center"> <h1 align="center">The Tech Bites Daily</h1>
An Useful Python project that reads technology, security and AI news from trusted RSS feeds, ranks the stories, and emails you a once-a-day digest laid out like an old newspaper: blackletter masthead, ruled columns, sepia photos, and a dark "Top Story" box.

It runs for free on **GitHub Actions**, and a free **Cloudflare Worker** starts it at an exact time every day (GitHub's own scheduler is often hours late).

- [What you get](#what-you-get)
- [How it works](#how-it-works)
- [Project structure](#project-structure)
- [Quick start (run it locally)](#quick-start-run-it-locally)
- [Deploy on GitHub Actions](#deploy-on-github-actions)
- [Schedule it exactly with Cloudflare](#schedule-it-exactly-with-cloudflare)
- [Configuration reference](#configuration-reference)
- [Customising](#customising)
- [Troubleshooting](#troubleshooting)
- [Security notes](#security-notes)
- [Limitations](#limitations)

---

## What you get

- **~25 trusted sources** across security, tech news, AI, infrastructure, engineering, business, India policy and the developer community.
- **Ranking by simple, transparent rules**: a per-feed base score plus keyword points (for example "actively exploited" or "zero-day").
- **Newspaper layout**: dateline, issue number, a dark "Top Story" box, ruled two-column sections, and a "Priority" marker for the highest-ranked stories.
- **Photos in a vintage look**: pulled from each article, cropped and converted to sepia. In each section about 60% of stories get a picture and the rest are text-only.
- **"Talk of the Town" board**: the most-voted Hacker News stories as a ranked list with a points stamp (a dark "HOT" stamp at 500+ points), a short description of the article, the comment count, and links to both the article and the discussion.
- **Security alerts from official data**: critical GitHub advisories and CISA's Known Exploited Vulnerabilities list, read from their JSON data rather than RSS.
- **Mobile friendly**: columns stack into one on phones, with a separate compact masthead.
- **Resilient**: feeds and images are retried when a connection drops, so one bad request doesn't lose a source.
- **Plain-text fallback** for mail clients that don't show HTML.

## How it works

1. **Fetch.** For every entry in `FEEDS`, the script reads the feed with `feedparser` (retrying up to 3 times if the connection drops) and keeps the title, link, publish time and the first 700 characters of the description. Two sources have no usable RSS feed, so they are read as JSON instead (marked with a `format` field in `FEEDS`):
   - **GitHub Security Advisories** from GitHub's API: the newest *critical* reviewed advisories, at most 5.
   - **CISA Known Exploited** from CISA's Known Exploited Vulnerabilities catalogue (with a GitHub mirror as backup): flaws added within the look-back window, at most 5. CISA publishes only the date, so these are timed at the end of that day.
2. **Filter.** Entries with no link are skipped, duplicate links are counted once, and anything older than `LOOKBACK_HOURS` is dropped. Entries with no date are kept but lose 2 points.
3. **Score and combine.** `score = feed base_score + points for every keyword found in the title or description` (each keyword counts once). Matching is plain text, so `chip` also matches "chipset". Headlines from different outlets about the same event are then combined into one story that earns `CROSS_SOURCE_BONUS` points for each extra outlet, so big stories reported everywhere rise to the top and show an "Also reported by ..." line.
4. **Select.** Stories are sorted by score (newer first when tied). The top `MAX_PER_CATEGORY` (3) of each category are taken first so every topic is represented, then leftover slots up to `MAX_STORIES` go to the best remaining stories from any category.
5. **Order.**
   - The **lead story** is the highest-scoring story among the top 6 that has a photo.
   - **Sections** appear in the order of their best story's rank.
   - Inside a section, picture stories come first, then text-only ones, filling left to right.
   - **Talk of the Town** (the Hacker News section) is the exception: it is a single-column board sorted by **points**, highest first, and it never supplies the lead story or takes a photo slot.
   - The email is ranked by score, **not** in time order. The time on each story is its publish time in IST.
6. **Photos and descriptions.** The script looks for a photo in the feed entry, then falls back to the article page's `og:image`. Photos are downloaded (with one retry), cropped, converted to sepia and attached to the email as inline images. Hacker News items carry no text, so for those the script opens each article and uses its own page description (`og:description` or the meta description) as the short summary. If a site blocks the request, that story simply shows without a description.
7. **Send.** The email is sent through Gmail SMTP with a long timeout and automatic retries.

## Project structure

```
tech-bites/
├── briefing.py                  # the whole program
├── requirements.txt             # feedparser, Pillow
├── assets/
│   ├── masthead.png             # wide blackletter masthead for desktop (served via jsDelivr)
│   └── masthead-mobile.png      # compact masthead shown on phones
├── .github/
│   └── workflows/
│       └── daily.yml            # GitHub Actions workflow (manual trigger only)
└── README.md
```

`requirements.txt` should contain:

```
feedparser
Pillow
```

Pillow is optional at runtime (without it the email still sends, just without photos), but you almost certainly want it.

---

## Quick start (run it locally)

### 1. Requirements

- Python 3.10 or newer
- A Gmail account with **2-Step Verification** turned on
- A Gmail **App Password** (a 16-character password just for this script)

### 2. Create a Gmail App Password

1. Go to your Google Account → **Security** → **2-Step Verification** (turn it on if it isn't).
2. Open **App passwords**, create one (name it "Tech Bites"), and copy the 16 characters.
3. Never put it in the code or paste it into chats, issues or screenshots. If it leaks, delete it in the same page and create a new one.

### 3. Install

```bash
git clone https://github.com/mohamed-ashif-m/tech-bites.git
cd tech-bites
pip install -r requirements.txt
```

### 4. Set your credentials as environment variables

These last only for the current terminal session. Nothing is written to disk.

**Windows PowerShell**

```powershell
$env:EMAIL_ADDRESS = "you@gmail.com"
$env:EMAIL_APP_PASSWORD = "your16characterpassword"
$env:EMAIL_TO = "you@gmail.com"      # optional: defaults to EMAIL_ADDRESS
python briefing.py
```

**macOS / Linux**

```bash
export EMAIL_ADDRESS="you@gmail.com"
export EMAIL_APP_PASSWORD="your16characterpassword"
export EMAIL_TO="you@gmail.com"      # optional
python briefing.py
```

### 5. Preview without sending (optional)

Save this as `preview.py` next to `briefing.py`. It builds the real digest and opens it in your browser without logging in to Gmail:

```python
import base64
import os
import webbrowser

import briefing as b

stories = b.collect_stories()
images = b.fetch_images(stories)
html_body, inline = b.make_email(stories, images)

# Swap the inline-attachment links for embedded images so a browser can show them
for cid, data, subtype, _ in inline:
    html_body = html_body.replace(
        "cid:" + cid[1:-1],
        f"data:image/{subtype};base64," + base64.b64encode(data).decode(),
    )

with open("preview.html", "w", encoding="utf-8") as f:
    f.write(html_body)
webbrowser.open("file://" + os.path.abspath("preview.html"))
```

Browsers show the vintage web fonts. Gmail falls back to Georgia, so the real email looks slightly different.

---

## Deploy on GitHub Actions

### 1. Add repository secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `EMAIL_ADDRESS` | the Gmail address that sends the digest |
| `EMAIL_APP_PASSWORD` | the 16-character app password |
| `EMAIL_TO` | where to deliver the digest (can be the same address) |

### 2. The workflow

The workflow already lives in the repo at `.github/workflows/daily.yml`. It has only a `workflow_dispatch:` trigger (no `schedule:`), installs the requirements, and runs `python briefing.py` with the three secrets above as environment variables.

### 3. Test it

Repo → **Actions → Daily Technology Briefing → Run workflow**. A run takes roughly 15 to 60 seconds and the email arrives when it finishes.

> **Why there is no `schedule:` block.** GitHub's built-in scheduler is best-effort. Runs can be delayed by hours or skipped, and it pauses entirely on inactive repos. If you keep both a `schedule:` and the Cloudflare trigger, you will get two emails a day. Use only one.

---

## Schedule it exactly with Cloudflare

A Cloudflare Worker (free plan) calls GitHub's API at a fixed time, which starts the workflow immediately. Cloudflare cron triggers run in **UTC**.

### Pick your time in UTC

`UTC = your local time − your offset`. For India (UTC+5:30):

| Local time (IST) | Cron (UTC) |
|---|---|
| 06:00 | `30 0 * * *` |
| 07:00 | `30 1 * * *` |
| 08:00 | `30 2 * * *` |
| 09:00 | `30 3 * * *` |
| 18:00 | `30 12 * * *` |

The five fields are `minute hour day-of-month month day-of-week`.

### Step 1: Create a GitHub token

1. GitHub → your profile → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. **Repository access:** *Only select repositories* → choose `tech-bites`.
3. **Permissions → Repository permissions → Actions: Read and write.** Leave everything else at "No access".
4. Set an **expiration** (for example 1 year) and add a calendar reminder to renew it.
5. Generate and copy the token. You will not see it again.

### Step 2: Create the Worker

1. Sign in to the Cloudflare dashboard → **Workers & Pages → Create → Workers**.
2. Choose the **Hello World** template, name it (for example `tech-bites-daily`) and deploy.
3. Click **Edit code**, replace everything with the code below, and **Deploy**.

```js
const REPO = "mohamed-ashif-m/tech-bites";
const WORKFLOW = "daily.yml";

export default {
  // Runs on the cron schedule
  async scheduled(event, env, ctx) {
    await triggerWorkflow(env);
  },

  // Optional manual test: https://<your-worker>.workers.dev/?key=YOUR_TEST_KEY
  async fetch(request, env) {
    const key = new URL(request.url).searchParams.get("key");
    if (!env.TEST_KEY || key !== env.TEST_KEY) {
      return new Response("Not found", { status: 404 });
    }
    return new Response(await triggerWorkflow(env));
  },
};

async function triggerWorkflow(env) {
  const res = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "tech-bites-cron",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main" }),
    }
  );

  if (res.status !== 204) {
    const detail = await res.text();
    console.error(`GitHub responded ${res.status}: ${detail}`);
    throw new Error(`Dispatch failed with ${res.status}`);
  }
  console.log("Workflow dispatched");
  return "Workflow dispatched";
}
```

If your repo, workflow file name or default branch differ, change `REPO`, `WORKFLOW` and `ref`.

### Step 3: Add the secrets

Worker → **Settings → Variables and Secrets → Add**, type **Secret**:

| Name | Value |
|---|---|
| `GITHUB_TOKEN` | the token from Step 1 |
| `TEST_KEY` | any long random string (used only for the test in Step 5) |

Deploy again if the dashboard asks you to.

### Step 4: Add the cron trigger

Worker → **Settings → Triggers → Cron Triggers → Add**, and enter your cron expression from the table above, for example `30 0 * * *` for 06:00 IST.

(Cloudflare moves menu items around now and then. Look for "Triggers" or "Cron Triggers" if the labels differ.)

### Step 5: Test

Open `https://<worker-name>.<your-subdomain>.workers.dev/?key=YOUR_TEST_KEY`.

- The page should say **Workflow dispatched**.
- A new run should appear in **GitHub → Actions** within seconds, labelled "Manually run by *your-username*".
- Check the Worker's **Observability → Logs** for "Workflow dispatched".

### Step 6: Lock it down

Delete the `TEST_KEY` secret. With no key set, the Worker's public URL always returns 404 and only the cron trigger can start the workflow.

### Checking a scheduled run

The next day, look for a scheduled event in the Worker's Observability and a new run in GitHub Actions. Scheduled runs are labelled "Manually run by *your-username*" because they come from the API.

### Worker troubleshooting

| Symptom | Likely cause |
|---|---|
| Test URL returns 404 | wrong or missing `?key=`, or `TEST_KEY` was deleted |
| Logs show `401` | token is wrong, expired or not saved as `GITHUB_TOKEN` |
| Logs show `403` | the token lacks **Actions: Read and write** or isn't granted to this repo |
| Logs show `404` | wrong `REPO`, `WORKFLOW` file name or `ref`, or the workflow has no `workflow_dispatch:` |
| Logs show `422` | the `ref` branch doesn't exist (check it is `main`) |
| Runs stop one day | the token expired: create a new one and update `GITHUB_TOKEN` |

---

## Configuration reference

All settings are constants near the top of `briefing.py`.

| Setting | What it does | Suggested |
|---|---|---|
| `FEEDS` | list of sources: `name`, `url`, `category`, `base_score` (plus `format` for the two JSON sources) | see [Customising](#customising) |
| `KEYWORDS` | words and the points they add to a story's score | edit freely |
| `MAX_STORIES` | maximum stories in one email | `30` |
| `MAX_PER_CATEGORY` | guaranteed slots per category before the leftover fill | `3` |
| `CROSS_SOURCE_BONUS` / `CROSS_SOURCE_BONUS_MAX` | points added per extra outlet covering the same story, and the cap | `4` / `12` |
| `LOOKBACK_HOURS` | how far back to look for stories (also applies to the GitHub and CISA security sources) | `24` for a daily digest (the script ships with `48`) |
| `IMAGE_SHARE` | share of each section's stories that get a photo | `0.6` (60%) |
| `MAX_IMAGES` | cap on photos downloaded per email | `22` |
| `EMAIL_WIDTH` | overall email width in pixels | `800` (use 700 or less for maximum compatibility) |
| `SMTP_TIMEOUT` / `SMTP_ATTEMPTS` | upload patience and retries | `120` / `3` |
| `FIRST_ISSUE` | date used for the "No. N" issue counter | your launch date |
| `MASTHEAD_URL` / `MASTHEAD_MOBILE_URL` | where the desktop and phone masthead images are loaded from | your jsDelivr links |

### A note on `LOOKBACK_HOURS`

A 24-hour window is measured from the moment the script runs. If a run is ever late, the stories published in the gap since the previous run can be missed. With 48 hours you'd see occasional repeats instead. A value of 26 to 30 is a reasonable compromise.

---

## Customising

### Add or remove a news source

Add an entry to `FEEDS`:

```python
{"name": "Example Blog", "url": "https://example.com/feed.xml",
 "category": "Tech News", "base_score": 2},
```

- Use a category name that already exists to put the story in an existing section, or invent a new one for a new section.
- `base_score` is how much you trust or prioritise the source (0 to 4 is typical).
- Test a feed first: `python -c "import feedparser; print(len(feedparser.parse('URL').entries))"`

### The Community section

Hacker News is read from `https://hnrss.org/frontpage?points=200`, so only stories with 200+ points appear. Change the number in that URL to be pickier or more generous. The "HOT" stamp threshold (500 points) is the `hot = ... >= 500` line in `make_email`. Stories in the `Community` category are shown in the Talk of the Town board; to turn the board off, delete that feed from `FEEDS`.

### Security sources

`GitHub Security Advisories` and `CISA Known Exploited` use `"format": "github_advisories"` and `"format": "cisa_kev"`. To see more or fewer items, change the `[:5]` at the end of `github_advisory_entries` and `cisa_kev_entries`. To include *high* as well as *critical* advisories, change `severity=critical` in the GitHub URL.

### Change what counts as important

Edit `KEYWORDS`. Every keyword found in a story's title or description adds its points once. Higher points push stories toward the top and into the "Priority" band (anything scoring at least two thirds of the day's top score).

### Change the look

- **Width:** `EMAIL_WIDTH`.
- **Colours:** `INK`, `PAPER`, `DESK`, `DARK` and related constants.
- **Photo share:** `IMAGE_SHARE`.
- **Masthead:** replace `assets/masthead.png` (about 1264 x 256 px works well) and, for phones, `assets/masthead-mobile.png` (about 1020 x 246 px), and make sure `MASTHEAD_URL` and `MASTHEAD_MOBILE_URL` point to them. Clients that don't support phone styles simply show the desktop masthead. jsDelivr caches files, so add a version to the URL (for example `@v2`) or wait a while after replacing the image.

---

## Troubleshooting

| Problem | What to check |
|---|---|
| `Could not read feed: ...` | the feed URL changed, or the site blocks scripts. The script already retried 3 times. Try the URL in a browser, find the current feed link, or try a browser-like `User-Agent` |
| A feed shows `fetched 0 entries` | the URL returns no items to a script, or (for the security sources) nothing new met the criteria in the look-back window. Check the URL in a browser |
| `Failed to fetch <source>` with `RemoteDisconnected` | a dropped connection on your side. It is retried automatically; if it repeats, check your network or VPN |
| Image lines such as `Image download failed ... timed out` | slow or blocked image hosts. Harmless: that story is shown without a photo |
| `og:image lookup failed ... 403` (for example OpenAI) | the publisher blocks scripts. Harmless: no photo for that story |
| A Talk of the Town story has no description | the article's site blocked the lookup or has no page description. The title and links still work |
| `SMTPAuthenticationError (535)` | wrong app password, or 2-Step Verification is off. Create a new app password |
| `SMTPServerDisconnected` or "write operation timed out" | the email is large and the upload was cut off. The script retries 3 times with a 120-second timeout; also check your internet connection |
| `KeyError: 'EMAIL_ADDRESS'` | the environment variables aren't set in this terminal (or the GitHub secrets are missing) |
| Email arrives with no photos | Pillow isn't installed, or the sites blocked the download (some publishers return 403) |
| Masthead image missing | the file isn't in the repo at `assets/masthead.png`, or `MASTHEAD_URL` is wrong. Open the URL in a browser to check |
| Headlines look blue | an old version of the script. Links must carry the ink colour inline; use the current `briefing.py` |
| Two emails per day | both a GitHub `schedule:` and the Cloudflare trigger are active. Remove the `schedule:` block |
| Email is clipped in Gmail | Gmail cuts the HTML text above roughly 100 KB (photos don't count). A normal issue is around 35 to 50 KB. If you hit it, reduce `MAX_STORIES` |
| Email is large (400 to 600 KB) | normal, because of the photos; Gmail allows 25 MB. For smaller mail, lower `MAX_IMAGES`, `IMAGE_SHARE`, the JPEG `quality` in `vintage_jpeg`, or the photo sizes in `make_email` |
| Email is very late with GitHub's scheduler | expected. Use the Cloudflare trigger instead |

---

## Security notes

- **Credentials never go in the code.** They live in environment variables locally and in GitHub Actions secrets in the cloud. If one is ever pasted somewhere public, revoke it immediately.
- **The Gmail app password stays inside GitHub.** Cloudflare never sees it.
- **The only secret Cloudflare holds is the GitHub token**, which is limited to one repo and the Actions permission. The worst a leaked token can do is start your workflow (extra emails and some used Actions minutes). It cannot read or change your code or your other secrets.
- **Turn on two-factor authentication** for both GitHub and Cloudflare. Anyone who gets into your Cloudflare account could change the Worker and read the token.
- **Delete `TEST_KEY`** after testing so the Worker has no public endpoint.
- **Rotate the GitHub token** when it expires, or sooner if you suspect a leak.

## Limitations

- Ranking is **keyword-based**, not a verified severity rating, and it can miss important stories or over-rank loosely related ones. Always read the original source before acting on a claim.
- Photos belong to their publishers. This project is meant for a personal digest, so don't republish the emails.
- Web fonts load in Apple Mail and iOS. Gmail and Outlook fall back to Georgia or Times, which still suits the newspaper look. The blackletter masthead is an image so it appears everywhere.
- Some feeds change URL or block automated requests, so check the log now and then for `Could not read feed` lines.
- The GitHub and CISA security sources depend on their public JSON endpoints. If one is renamed or rate-limited, that source will log a failure and the rest of the digest still sends.
