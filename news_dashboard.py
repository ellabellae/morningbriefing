#!/usr/bin/env python3
"""
Morning news dashboard.
Run each morning: python3 news_dashboard.py
Requires: pip install feedparser yfinance
"""

import feedparser
import re
import sys
import webbrowser
import html as html_lib
import http.server
import json
import os
import socketserver
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import yfinance as yf

# Some RSS feeds (e.g. Fierce Biotech) wrap titles in <a href="...">…</a>;
# strip tags and decode entities so titles render as plain text.
_TAG_RE = re.compile(r"<[^>]+>")


def clean_title(raw):
    if not raw:
        return "Untitled"
    text = _TAG_RE.sub("", str(raw))
    text = html_lib.unescape(text)
    return text.strip() or "Untitled"

# ── RSS feed configs ────────────────────────────────────────
FEEDS = [
    {
        "source": "Endpoints News",
        "label": "Pharma",
        "url": "https://endpoints.news/channel/pharma/feed/",
        "is_podcast": False,
        "tag": "Pharma",
        "tag_class": "tag-pharma",
    },
    {
        "source": "Endpoints News",
        "label": "R&D / Biotech",
        "url": "https://endpoints.news/channel/rd/feed/",
        "is_podcast": False,
        "tag": "Biotech",
        "tag_class": "tag-biotech",
    },
    {
        "source": "Endpoints News",
        "label": "Health Tech / Medical Devices",
        "url": "https://endpoints.news/channel/health-tech/feed/",
        "is_podcast": False,
        "tag": "Medical Devices",
        "tag_class": "tag-devices",
    },
    {
        "source": "Fierce Biotech",
        "label": "Fierce Biotech",
        "url": "https://www.fiercebiotech.com/rss/xml",
        "is_podcast": False,
        "tag": "Biotech",
        "tag_class": "tag-biotech",
    },
    {
        "source": "Huberman Lab",
        "label": "Huberman Lab Podcast",
        "url": "https://feeds.megaphone.fm/hubermanlab",
        "is_podcast": True,
        "tag": "Podcast",
        "tag_class": "tag-podcast",
    },
    {
        "source": "Acquired",
        "label": "Acquired Podcast",
        "url": "https://feeds.transistor.fm/acquired",
        "is_podcast": True,
        "tag": "Podcast",
        "tag_class": "tag-podcast",
    },
]

PODCAST_EPISODE_LIMIT = 3
NEWS_LOOKBACK_HOURS = 36

# News-grid placement order (matches CSS classes in _CSS).
SOURCE_ORDER = ["Endpoints News", "Fierce Biotech", "Huberman Lab", "Acquired"]
SOURCE_GRID_CLASS = {
    "Endpoints News": "grid-endpoints",
    "Fierce Biotech": "grid-fierce",
    "Huberman Lab":   "grid-huberman",
    "Acquired":       "grid-acquired",
}

# ── Stocks ──────────────────────────────────────────────────
STOCK_GROUPS = [
    {
        "label": "Market & Tech",
        "tickers": [
            ("^GSPC", "S&P 500"),
            ("AAPL",  "Apple"),
            ("NVDA",  "Nvidia"),
        ],
    },
    {
        "label": "Pharma & Health",
        "tickers": [
            ("MRK",  "Merck"),
            ("JNJ",  "Johnson & Johnson"),
            ("NVO",  "Novo Nordisk"),
            ("PFE",  "Pfizer"),
            ("LLY",  "Eli Lilly"),
            ("HIMS", "Hims & Hers"),
            ("OSCR", "Oscar Health"),
        ],
    },
]

# ── Watchlist ───────────────────────────────────────────────
DEFAULT_WATCHLIST = ["Oura", "Anthropic", "OpenAI", "Whoop", "Ro", "Pomelo Care"]

# Per-company Google News query overrides for ambiguous names. Falls back to
# the bare company name when not present (used for user-added companies).
WATCHLIST_QUERIES = {
    "Oura":        '"Oura Ring" health',
    "Anthropic":   'Anthropic Claude AI',
    "OpenAI":      'OpenAI ChatGPT',
    "Whoop":       '"Whoop" wearable fitness',
    "Ro":          '"Ro" telehealth pharmacy weight loss',
    "Pomelo Care": '"Pomelo Care" maternal health',
}

# Topic keywords for post-fetch relevance filtering. A headline must contain
# at least one of these (in title or summary, case-insensitive) to be shown.
# Companies without an entry skip filtering entirely.
WATCHLIST_TOPICS = {
    "Oura":        ["ring", "health", "wearable", "sleep", "fitness", "sensor", "oura"],
    "Anthropic":   ["ai", "claude", "anthropic", "amodei", "model", "llm", "agi"],
    "OpenAI":      ["ai", "chatgpt", "gpt", "altman", "openai", "sora", "dall"],
    "Whoop":       ["wearable", "fitness", "health", "whoop", "tracker", "recovery", "strain"],
    "Ro":          ["telehealth", "glp", "weight", "pharmacy", "roman", "wegovy", "ozempic", "compound", "pill", "obesity"],
    "Pomelo Care": ["maternal", "maternity", "pregnancy", "pomelo", "prenatal", "postpartum", "ob ", "obgyn", "birth"],
}

# ── Paul Graham essay rotation ──────────────────────────────
PG_ESSAYS = [
    ("How to Do Great Work",                   "http://paulgraham.com/greatwork.html"),
    ("Cities and Ambition",                    "http://paulgraham.com/cities.html"),
    ("Do Things That Don't Scale",             "http://paulgraham.com/ds.html"),
    ("Maker's Schedule, Manager's Schedule",   "http://paulgraham.com/makersschedule.html"),
    ("How to Start a Startup",                 "http://paulgraham.com/start.html"),
    ("Hackers and Painters",                   "http://paulgraham.com/hp.html"),
    ("What You Can't Say",                     "http://paulgraham.com/say.html"),
    ("The Top Idea in Your Mind",              "http://paulgraham.com/top.html"),
    ("How to Lose Time and Money",             "http://paulgraham.com/selfindulgence.html"),
    ("Beating the Averages",                   "http://paulgraham.com/avg.html"),
    ("Why Nerds are Unpopular",                "http://paulgraham.com/nerds.html"),
    ("The Power of the Marginal",              "http://paulgraham.com/marginal.html"),
]

DICKINSON_QUOTES = [
    "Hope is the thing with feathers\nThat perches in the soul.",
    "Tell all the truth but tell it slant —\nSuccess in Circuit lies.",
    "I dwell in Possibility —\nA fairer House than Prose.",
    "Forever — is composed of Nows —",
    "Because I could not stop for Death —\nHe kindly stopped for me.",
    "I’m Nobody! Who are you?\nAre you — Nobody — too?",
    "The Brain — is wider than the Sky —",
    "There is no Frigate like a Book\nTo take us Lands away.",
    "Success is counted sweetest\nBy those who ne’er succeed.",
    "Much Madness is divinest Sense —\nTo a discerning Eye.",
    "This is my letter to the World\nThat never wrote to Me.",
    "The Soul selects her own Society —\nThen — shuts the Door.",
    "That it will never come again\nIs what makes life so sweet.",
    "To make a prairie it takes a clover and one bee.",
    "Not knowing when the Dawn will come,\nI open every Door.",
    "After great pain, a formal feeling comes —",
    "One need not be a Chamber — to be Haunted.",
    "Dying is a wild Night and a new Road.",
    "I felt a Funeral, in my Brain.",
    "Wild Nights — Wild Nights!\nWere I with thee!",
]


# ── Helpers ─────────────────────────────────────────────────
def pick_quote():
    idx = datetime.now().timetuple().tm_yday % len(DICKINSON_QUOTES)
    return DICKINSON_QUOTES[idx]


def pick_essay():
    idx = datetime.now().timetuple().tm_yday % len(PG_ESSAYS)
    return PG_ESSAYS[idx]


def parse_date(entry):
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            return datetime(*val[:6], tzinfo=timezone.utc)
    return None


def format_card_date(entry, is_podcast):
    dt = parse_date(entry)
    if not dt:
        return ""
    if is_podcast:
        return dt.strftime("%b %-d, %Y")
    return dt.strftime("%-I:%M %p · %b %-d")


def format_news_date(dt):
    if not dt:
        return ""
    return dt.strftime("%b %-d, %Y")


def escape(text):
    return html_lib.escape(str(text))


def js_safe_json(obj):
    # Avoid breaking out of <script> tags if any string contains "</...".
    return json.dumps(obj).replace("</", "<\\/")


def format_price(ticker, price):
    if price is None:
        return "—"
    formatted = f"{price:,.2f}"
    return formatted if ticker.startswith("^") else f"${formatted}"


def format_change(pct):
    if pct is None:
        return "—"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


# ── Fetchers ────────────────────────────────────────────────
# Sent on every outbound feed request to defeat upstream / CDN HTTP caches.
_NO_CACHE_HEADERS = {"Cache-Control": "no-cache", "Pragma": "no-cache"}


def fetch_feed(config):
    try:
        parsed = feedparser.parse(config["url"], request_headers=_NO_CACHE_HEADERS)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=NEWS_LOOKBACK_HOURS)
        entries = []
        for entry in parsed.entries:
            if config["is_podcast"]:
                entries.append(entry)
                if len(entries) >= PODCAST_EPISODE_LIMIT:
                    break
            else:
                pub = parse_date(entry)
                if pub is None or pub >= cutoff:
                    entries.append(entry)
        return {"config": config, "entries": entries, "error": None}
    except Exception as exc:
        return {"config": config, "entries": [], "error": str(exc)}


def fetch_all_feeds():
    with ThreadPoolExecutor(max_workers=len(FEEDS)) as ex:
        return list(ex.map(fetch_feed, FEEDS))


def fetch_stock(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period="5d")
        if hist.empty:
            return {"ticker": ticker_symbol, "price": None, "change_pct": None, "error": "no data"}
        closes = hist["Close"].dropna()
        if closes.empty:
            return {"ticker": ticker_symbol, "price": None, "change_pct": None, "error": "no data"}
        price = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) >= 2 else price
        change_pct = ((price - prev) / prev) * 100 if prev else 0.0
        return {"ticker": ticker_symbol, "price": price, "change_pct": change_pct, "error": None}
    except Exception as exc:
        return {"ticker": ticker_symbol, "price": None, "change_pct": None, "error": str(exc)}


def fetch_all_stocks():
    all_tickers = [t for grp in STOCK_GROUPS for t, _ in grp["tickers"]]
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(fetch_stock, all_tickers))
    return {r["ticker"]: r for r in results}


def fetch_company_news(company_name):
    query = WATCHLIST_QUERIES.get(company_name, company_name)
    topics = [t.lower() for t in WATCHLIST_TOPICS.get(company_name, [])]
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        parsed = feedparser.parse(url, request_headers=_NO_CACHE_HEADERS)
        candidates = []
        for entry in parsed.entries[:25]:
            title = clean_title(entry.get("title"))
            summary = entry.get("summary", "") or ""
            haystack = f"{title} {summary}".lower()
            score = sum(1 for t in topics if t in haystack) if topics else 1
            candidates.append({
                "title": title,
                "link":  entry.get("link", "#"),
                "date":  format_news_date(parse_date(entry)),
                "score": score,
            })
        if topics:
            chosen = [c for c in candidates if c["score"] > 0][:2]
        else:
            chosen = candidates[:2]
        items = [{"title": c["title"], "link": c["link"], "date": c["date"]} for c in chosen]
        return {"company": company_name, "items": items, "error": None}
    except Exception as exc:
        return {"company": company_name, "items": [], "error": str(exc)}


def fetch_all_watchlist_news():
    with ThreadPoolExecutor(max_workers=len(DEFAULT_WATCHLIST)) as ex:
        results = list(ex.map(fetch_company_news, DEFAULT_WATCHLIST))
    return {r["company"]: r for r in results}


# ── CSS / JS ────────────────────────────────────────────────
# Regular triple-quoted strings: their { } are literal CSS/JS, not Python f-string.
_CSS = """
    :root {
      --bg:     #ffffff;
      --card:   #ffffff;
      --border: #e5e3de;
      --ink:    #1c1b19;
      --muted:  #6b6860;
      --link:   #1a56db;
      --tag-bg: #eeecea;
    }
    [data-theme="dark"] {
      --bg:     #141413;
      --card:   #1e1d1b;
      --border: #2e2d2a;
      --ink:    #e8e6e1;
      --muted:  #8a8780;
      --link:   #6ea8fe;
      --tag-bg: #29281f;
    }

    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    html, body { height: auto; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.55;
      overflow: auto;
      display: block;
      padding: 1.25rem 1.5rem 2rem;
      transition: background 0.2s, color 0.2s;
    }
    .wrap { max-width: none; width: 100%; }

    /* ── Header ──────────────────────────────────────────── */
    header {
      position: relative;
      text-align: center;
      padding-bottom: 0.85rem;
      border-bottom: 1px solid var(--border);
      margin-bottom: 0.65rem;
    }
    .greeting { font-size: 0.75rem; color: var(--muted); margin-bottom: 0.15rem; }
    header h1 { font-size: 1.2rem; font-weight: 700; letter-spacing: -0.02em; }
    blockquote { margin-top: 0.6rem; }
    blockquote p {
      font-size: 0.77rem;
      font-style: italic;
      color: var(--muted);
      white-space: pre-line;
    }
    blockquote cite {
      display: block;
      font-size: 0.68rem;
      color: var(--muted);
      margin-top: 0.2rem;
      font-style: normal;
    }
    .theme-toggle {
      position: absolute; top: 0; right: 0;
      font-size: 0.68rem; font-weight: 600;
      text-transform: uppercase; letter-spacing: 0.06em;
      padding: 0.28rem 0.65rem;
      border: 1px solid var(--border); border-radius: 5px;
      background: var(--tag-bg); color: var(--muted); cursor: pointer;
    }
    .theme-toggle:hover { color: var(--ink); border-color: var(--muted); }

    /* ── Keyword panel ───────────────────────────────────── */
    .kw-panel {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 0.65rem;
    }
    .kw-panel > summary {
      font-size: 0.66rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.09em;
      color: var(--muted); padding: 0.6rem 1.1rem;
      cursor: pointer; user-select: none;
      list-style: none;
      display: flex; justify-content: space-between; align-items: center;
    }
    .kw-panel > summary::-webkit-details-marker { display: none; }
    .kw-panel > summary::after { content: '+'; font-size: 1rem; font-weight: 400; }
    .kw-panel[open] > summary::after { content: '−'; }
    .kw-panel-body { padding: 0.2rem 1.1rem 0.75rem; border-top: 1px solid var(--border); }
    .kw-chips {
      display: flex; flex-wrap: wrap; gap: 0.4rem;
      margin: 0.6rem 0 0.55rem; min-height: 1.5rem;
    }
    .kw-chip {
      display: inline-flex; align-items: center; gap: 0.25rem;
      font-size: 0.72rem; background: var(--tag-bg); color: var(--ink);
      padding: 0.18rem 0.4rem 0.18rem 0.6rem;
      border-radius: 99px; border: 1px solid var(--border);
    }
    .kw-remove {
      background: none; border: none; cursor: pointer;
      color: var(--muted); font-size: 1rem; padding: 0;
      line-height: 1; display: flex; align-items: center;
    }
    .kw-remove:hover { color: var(--ink); }
    .kw-input-row { display: flex; gap: 0.5rem; }
    .kw-input {
      flex: 1; font-size: 0.8rem; padding: 0.3rem 0.6rem;
      border: 1px solid var(--border); border-radius: 5px;
      background: var(--bg); color: var(--ink); outline: none;
    }
    .kw-input:focus { border-color: var(--link); }
    .kw-add {
      font-size: 0.76rem; padding: 0.3rem 0.8rem;
      border: 1px solid var(--border); border-radius: 5px;
      background: var(--tag-bg); color: var(--ink);
      cursor: pointer; white-space: nowrap;
    }
    .kw-add:hover { background: var(--border); }

    /* ── News Grid ───────────────────────────────────────── */
    .grid {
      height: 60vh;
      min-height: 380px;
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      grid-template-rows: repeat(2, 1fr);
      gap: 0.65rem;
      margin-bottom: 1rem;
    }
    section {
      overflow-y: auto;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.85rem 1rem;
    }
    .grid-endpoints { grid-column: 1;     grid-row: 1 / 3; }
    .grid-fierce    { grid-column: 2 / 4; grid-row: 1; }
    .grid-huberman  { grid-column: 2;     grid-row: 2; }
    .grid-acquired  { grid-column: 3;     grid-row: 2; }
    section h2 {
      font-size: 0.62rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.09em;
      color: var(--muted); margin-bottom: 0.55rem;
    }

    /* ── Article cards ───────────────────────────────────── */
    .item-card {
      display: flex; align-items: baseline; gap: 0.6rem;
      background: var(--card); border: 1px solid var(--border);
      border-radius: 7px; padding: 0.55rem 0.8rem; margin-bottom: 0.35rem;
      transition: box-shadow 0.15s ease, transform 0.15s ease, border-color 0.15s ease;
    }
    .item-card:hover {
      box-shadow: 0 2px 8px rgba(0,0,0,0.07);
      transform: translateY(-1px);
      border-color: #c0bdb7;
    }
    [data-theme="dark"] .item-card:hover {
      box-shadow: 0 2px 10px rgba(0,0,0,0.35);
      border-color: #444240;
    }
    .card-title {
      flex: 1; font-size: 0.825rem;
      color: var(--ink); text-decoration: none; min-width: 0;
    }
    .card-title:hover { color: var(--link); }
    .card-date { font-size: 0.67rem; color: var(--muted); white-space: nowrap; flex-shrink: 0; }

    /* ── Category tags ───────────────────────────────────── */
    .tag {
      font-size: 0.58rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.06em;
      padding: 0.16rem 0.45rem; border-radius: 4px;
      flex-shrink: 0; white-space: nowrap;
    }
    .tag-pharma  { color: #1d4ed8; background: #eff6ff; }
    .tag-biotech { color: #15803d; background: #f0fdf4; }
    .tag-devices { color: #6d28d9; background: #f5f3ff; }
    .tag-podcast { color: #c2410c; background: #fff7ed; }
    .tag-essay   { color: #991b1b; background: #fef2f2; }
    [data-theme="dark"] .tag-pharma  { color: #93c5fd; background: #1e3a5f; }
    [data-theme="dark"] .tag-biotech { color: #86efac; background: #14532d; }
    [data-theme="dark"] .tag-devices { color: #c4b5fd; background: #2e1065; }
    [data-theme="dark"] .tag-podcast { color: #fdba74; background: #431407; }
    [data-theme="dark"] .tag-essay   { color: #fca5a5; background: #450a0a; }

    /* ── Paul Graham bar ─────────────────────────────────── */
    .pg-bar {
      display: flex; align-items: center; gap: 0.65rem;
      background: var(--card); border: 1px solid var(--border);
      border-radius: 8px; padding: 0.5rem 1rem;
      margin-bottom: 0.65rem;
    }
    .pg-link {
      font-size: 0.83rem; color: var(--ink); text-decoration: none;
      flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .pg-link:hover { color: var(--link); }
    .pg-source { font-size: 0.68rem; color: var(--muted); white-space: nowrap; }

    /* ── Keyword highlight ───────────────────────────────── */
    mark { background: #fef9c3; color: inherit; font-weight: 600; border-radius: 2px; padding: 0 1px; }
    [data-theme="dark"] mark { background: #78350f; color: #fde68a; }

    /* ── Stocks ──────────────────────────────────────────── */
    .stocks-section {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.85rem 1rem;
      margin-bottom: 1rem;
    }
    .stocks-section h2 {
      font-size: 0.62rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.09em;
      color: var(--muted); margin-bottom: 0.65rem;
    }
    .stock-group-label {
      font-size: 0.6rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.07em;
      color: var(--muted); margin-bottom: 0.4rem; margin-top: 0.65rem;
    }
    .stock-group-label:first-of-type { margin-top: 0; }
    .stock-row {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
      gap: 0.5rem;
      margin-bottom: 0.1rem;
    }
    .stock-card {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 7px;
      padding: 0.6rem 0.8rem;
      display: flex; flex-direction: column; gap: 0.2rem;
    }
    .stock-ticker {
      font-size: 0.72rem; font-weight: 700; color: var(--ink);
      letter-spacing: 0.03em;
    }
    .stock-name {
      font-size: 0.6rem; color: var(--muted); white-space: nowrap;
      overflow: hidden; text-overflow: ellipsis;
    }
    .stock-price {
      font-size: 0.88rem; font-weight: 600; color: var(--ink);
      margin-top: 0.1rem;
    }
    .stock-change { font-size: 0.72rem; font-weight: 600; }
    .up   { color: #15803d; }
    .down { color: #b91c1c; }
    [data-theme="dark"] .up   { color: #86efac; }
    [data-theme="dark"] .down { color: #fca5a5; }

    /* ── Watchlist ───────────────────────────────────────── */
    .watchlist-section {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.85rem 1rem;
      margin-bottom: 1rem;
    }
    .watchlist-section h2 {
      font-size: 0.62rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.09em;
      color: var(--muted); margin-bottom: 0.65rem;
    }
    .watchlist-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 0.75rem;
    }
    .watchlist-card {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.85rem 1rem;
    }
    .watchlist-card-header {
      display: flex; align-items: center; gap: 0.5rem;
      margin-bottom: 0.4rem;
    }
    .watchlist-company { font-size: 0.88rem; font-weight: 700; color: var(--ink); }
    .private-badge {
      font-size: 0.56rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.06em;
      padding: 0.14rem 0.4rem; border-radius: 4px;
      color: #6d28d9; background: #f5f3ff;
      white-space: nowrap; flex-shrink: 0;
    }
    [data-theme="dark"] .private-badge { color: #c4b5fd; background: #2e1065; }
    .watchlist-news {
      border-top: 1px solid var(--border);
      padding-top: 0.5rem; margin-top: 0.4rem;
    }
    .watchlist-news-item { margin-bottom: 0.35rem; }
    .watchlist-news-item a {
      font-size: 0.78rem; color: var(--ink); text-decoration: none;
      display: block; line-height: 1.4;
    }
    .watchlist-news-item a:hover { color: var(--link); }
    .watchlist-news-item .news-date {
      font-size: 0.63rem; color: var(--muted); margin-top: 0.1rem;
    }
    .news-loading, .news-empty {
      font-size: 0.72rem; color: var(--muted); font-style: italic;
    }
    .wl-add-row { display: flex; gap: 0.5rem; margin-bottom: 0.85rem; }
    .wl-input {
      flex: 1; font-size: 0.8rem; padding: 0.3rem 0.6rem;
      border: 1px solid var(--border); border-radius: 5px;
      background: var(--bg); color: var(--ink); outline: none;
    }
    .wl-input:focus { border-color: var(--link); }
    .wl-add {
      font-size: 0.76rem; padding: 0.3rem 0.8rem;
      border: 1px solid var(--border); border-radius: 5px;
      background: var(--tag-bg); color: var(--ink);
      cursor: pointer; white-space: nowrap;
    }
    .wl-add:hover { background: var(--border); }
    .wl-remove {
      background: none; border: none; cursor: pointer;
      color: var(--muted); font-size: 1.15rem; padding: 0;
      line-height: 1; margin-left: auto;
      display: flex; align-items: center;
    }
    .wl-remove:hover { color: var(--ink); }

    /* ── Tabs ────────────────────────────────────────────── */
    .tabs {
      display: flex; gap: 0.4rem;
      margin-bottom: 0.65rem;
    }
    .tab-button {
      font-size: 0.7rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.08em;
      padding: 0.42rem 0.95rem;
      border: 1px solid var(--border); border-radius: 7px;
      background: var(--card); color: var(--muted);
      cursor: pointer;
      transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
    }
    .tab-button:hover { color: var(--ink); border-color: var(--muted); }
    .tab-button.active {
      background: var(--tag-bg); color: var(--ink);
      border-color: var(--muted);
    }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }

    /* ── Reading Journal ─────────────────────────────────── */
    .journal-note-block {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.85rem 1rem;
      margin-bottom: 1rem;
    }
    .journal-note-label {
      font-size: 0.62rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.09em;
      color: var(--muted); margin-bottom: 0.5rem;
      display: flex; justify-content: space-between; align-items: center;
    }
    .journal-save-status {
      font-size: 0.62rem; color: var(--muted);
      font-weight: 400; letter-spacing: 0; text-transform: none;
      font-style: italic;
    }
    .journal-textarea {
      width: 100%; min-height: 220px;
      font-family: inherit; font-size: 0.85rem; line-height: 1.55;
      padding: 0.7rem 0.85rem;
      background: var(--bg); color: var(--ink);
      border: 1px solid var(--border); border-radius: 7px;
      resize: vertical; outline: none;
      transition: border-color 0.15s ease;
    }
    .journal-textarea:focus { border-color: var(--link); }
    .journal-past-section {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.85rem 1rem;
      margin-bottom: 1rem;
    }
    .journal-past-section h2 {
      font-size: 0.62rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.09em;
      color: var(--muted); margin-bottom: 0.65rem;
    }
    .journal-search {
      width: 100%; font-size: 0.8rem;
      padding: 0.35rem 0.65rem;
      border: 1px solid var(--border); border-radius: 5px;
      background: var(--bg); color: var(--ink); outline: none;
      margin-bottom: 0.7rem;
      transition: border-color 0.15s ease;
    }
    .journal-search:focus { border-color: var(--link); }
    .journal-entry-card {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 7px;
      padding: 0.7rem 0.9rem;
      margin-bottom: 0.45rem;
      transition: box-shadow 0.15s ease, transform 0.15s ease, border-color 0.15s ease;
    }
    .journal-entry-card:hover {
      box-shadow: 0 2px 8px rgba(0,0,0,0.07);
      transform: translateY(-1px);
      border-color: #c0bdb7;
    }
    [data-theme="dark"] .journal-entry-card:hover {
      box-shadow: 0 2px 10px rgba(0,0,0,0.35);
      border-color: #444240;
    }
    .journal-entry-header {
      display: flex; align-items: baseline; gap: 0.6rem;
      margin-bottom: 0.35rem;
    }
    .journal-entry-title {
      flex: 1; font-size: 0.83rem; font-weight: 600;
      color: var(--ink); text-decoration: none;
      min-width: 0;
    }
    .journal-entry-title:hover { color: var(--link); }
    .journal-entry-date {
      font-size: 0.65rem; color: var(--muted);
      white-space: nowrap; flex-shrink: 0;
    }
    .journal-entry-note {
      font-size: 0.78rem; color: var(--ink);
      white-space: pre-wrap; line-height: 1.5;
    }

    /* ── Misc ────────────────────────────────────────────── */
    .msg { font-size: 0.8rem; color: var(--muted); padding: 0.25rem 0; }
    .error { color: #b91c1c; }
    [data-theme="dark"] .error { color: #fca5a5; }
    footer {
      text-align: center; font-size: 0.67rem; color: var(--muted);
      padding: 0.4rem 0 0; border-top: 1px solid var(--border);
    }
"""

# Raw string: backslashes in regex literals are preserved as-is for JavaScript.
# DEFAULT_COMPANIES and WATCHLIST_NEWS_CACHE are injected by build_html as a
# data prelude before this script runs.
_JS_COMMON = r"""
    /* ── Theme ───────────────────────────────────────────── */
    function initTheme() {
      const t = localStorage.getItem('theme') || 'light';
      document.documentElement.setAttribute('data-theme', t);
      document.querySelector('.theme-toggle').textContent = t === 'dark' ? 'Light' : 'Dark';
    }
    function toggleTheme() {
      const cur = document.documentElement.getAttribute('data-theme');
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('theme', next);
      document.querySelector('.theme-toggle').textContent = next === 'dark' ? 'Light' : 'Dark';
    }

    /* ── Greeting ─────────────────────────────────────────── */
    function setGreeting() {
      const h = new Date().getHours();
      const s = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
      document.getElementById('greeting').textContent = s + ', Ella.';
    }

    /* ── Today's date ─────────────────────────────────────── */
    // Re-derives the date from the client clock so a tab that's been left
    // open across midnight (or a stubbornly cached page) self-corrects.
    function setDate() {
      const formatted = new Date().toLocaleDateString('en-US', {
        weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
      });
      const el = document.getElementById('today-date');
      if (el) el.textContent = formatted;
      document.title = 'Morning Briefing · ' + formatted;
    }

    /* ── Util ─────────────────────────────────────────────── */
    function htmlEsc(str) {
      return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    /* ── Keywords ─────────────────────────────────────────── */
    const DEFAULT_KWS = ['AI', 'wearable', 'FDA', 'clinical trial', 'biotech', "women's health", 'digital health'];

    function loadKws() {
      const s = localStorage.getItem('kw_keywords');
      if (s === null) return [...DEFAULT_KWS];
      try {
        const parsed = JSON.parse(s);
        return Array.isArray(parsed) ? parsed : [...DEFAULT_KWS];
      } catch (e) {
        localStorage.removeItem('kw_keywords');
        return [...DEFAULT_KWS];
      }
    }
    function saveKws(kws) { localStorage.setItem('kw_keywords', JSON.stringify(kws)); }

    function applyHighlights(kws) {
      document.querySelectorAll('.card-title').forEach(el => {
        if (!el.dataset.orig) el.dataset.orig = el.textContent;
        const text = el.dataset.orig;
        if (!kws.length) { el.textContent = text; return; }
        const pattern = kws
          .map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
          .sort((a, b) => b.length - a.length)
          .join('|');
        el.innerHTML = htmlEsc(text).replace(new RegExp('(' + pattern + ')', 'gi'), '<mark>$1</mark>');
      });
    }

    function renderKws() {
      const kws = loadKws();
      const container = document.getElementById('kw-chips');
      container.innerHTML = '';
      kws.forEach(kw => {
        const chip = document.createElement('span');
        chip.className = 'kw-chip';
        chip.textContent = kw;
        const btn = document.createElement('button');
        btn.className = 'kw-remove';
        btn.textContent = '×';
        btn.setAttribute('aria-label', 'Remove ' + kw);
        btn.addEventListener('click', () => removeKw(kw));
        chip.appendChild(btn);
        container.appendChild(chip);
      });
      const ct = document.getElementById('kw-count');
      ct.textContent = kws.length ? '(' + kws.length + ')' : '';
      applyHighlights(kws);
    }

    function addKeyword() {
      const input = document.getElementById('kw-input');
      const kw = input.value.trim();
      if (!kw) return;
      const kws = loadKws();
      if (!kws.some(k => k.toLowerCase() === kw.toLowerCase())) {
        kws.push(kw);
        saveKws(kws);
      }
      input.value = '';
      renderKws();
    }

    function removeKw(kw) {
      saveKws(loadKws().filter(k => k !== kw));
      renderKws();
    }


    /* ── Watchlist (dynamic) ─────────────────────────────── */
    function loadCompanies() {
      const s = localStorage.getItem('watchlist_companies');
      if (s === null) return [...DEFAULT_COMPANIES];
      try {
        const parsed = JSON.parse(s);
        return Array.isArray(parsed) ? parsed : [...DEFAULT_COMPANIES];
      } catch (e) {
        localStorage.removeItem('watchlist_companies');
        return [...DEFAULT_COMPANIES];
      }
    }
    function saveCompanies(list) {
      localStorage.setItem('watchlist_companies', JSON.stringify(list));
    }

    function fmtNewsDate(str) {
      if (!str) return '';
      const d = new Date(str);
      return isNaN(d) ? str : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    }

    function renderNewsItems(items) {
      return items.map(it => `
        <div class="watchlist-news-item">
          <a href="${htmlEsc(it.link)}" target="_blank" rel="noopener">${htmlEsc(it.title)}</a>
          <div class="news-date">${htmlEsc(fmtNewsDate(it.date))}</div>
        </div>`).join('');
    }

    async function fetchCompanyNews(query) {
      // Prefer the local Python proxy when available — it bypasses CORS,
      // applies query enrichment, and filters by topic keywords.
      try {
        const r = await fetch('/api/news?q=' + encodeURIComponent(query));
        if (r.ok && (r.headers.get('Content-Type') || '').includes('application/json')) {
          return await r.json();
        }
      } catch (e) { /* proxy not running — fall through */ }
      // Direct browser fetch (will be CORS-blocked from file:// or any origin
      // that news.google.com does not whitelist).
      const url = 'https://news.google.com/rss/search?q=' + encodeURIComponent(query) + '&hl=en-US&gl=US&ceid=US:en';
      const res = await fetch(url);
      const xml = new DOMParser().parseFromString(await res.text(), 'text/xml');
      return Array.from(xml.querySelectorAll('item')).slice(0, 2).map(el => ({
        title: el.querySelector('title')?.textContent?.trim() || 'Untitled',
        link:  el.querySelector('link')?.textContent?.trim() || '#',
        date:  el.querySelector('pubDate')?.textContent?.trim() || '',
      }));
    }

    async function loadCompanyNewsInto(container, name) {
      const cacheKey = name.toLowerCase();
      if (WATCHLIST_NEWS_CACHE[cacheKey]) {
        const cached = WATCHLIST_NEWS_CACHE[cacheKey];
        container.innerHTML = cached.length
          ? renderNewsItems(cached)
          : '<div class="news-empty">No recent headlines.</div>';
        return;
      }
      try {
        const items = await fetchCompanyNews(name);
        container.innerHTML = items.length
          ? renderNewsItems(items)
          : '<div class="news-empty">No recent headlines.</div>';
      } catch (e) {
        container.innerHTML = '<div class="news-empty">News unavailable (CORS or network error).</div>';
      }
    }

    function renderWatchlist() {
      const companies = loadCompanies();
      const grid = document.getElementById('watchlist-grid');
      grid.innerHTML = '';
      companies.forEach(name => {
        const card = document.createElement('div');
        card.className = 'watchlist-card';
        card.innerHTML = `
          <div class="watchlist-card-header">
            <span class="watchlist-company"></span>
            <span class="private-badge">Private</span>
            <button class="wl-remove" type="button" aria-label="Remove">×</button>
          </div>
          <div class="watchlist-news">
            <div class="news-loading">Loading headlines&hellip;</div>
          </div>`;
        card.querySelector('.watchlist-company').textContent = name;
        card.querySelector('.wl-remove').addEventListener('click', () => removeCompany(name));
        const newsEl = card.querySelector('.watchlist-news');
        grid.appendChild(card);
        loadCompanyNewsInto(newsEl, name);
      });
    }

    function addCompany() {
      const input = document.getElementById('wl-input');
      const name = input.value.trim();
      if (!name) return;
      const list = loadCompanies();
      if (!list.some(n => n.toLowerCase() === name.toLowerCase())) {
        list.push(name);
        saveCompanies(list);
      }
      input.value = '';
      renderWatchlist();
    }

    function removeCompany(name) {
      saveCompanies(loadCompanies().filter(n => n !== name));
      renderWatchlist();
    }

    /* ── Tabs ─────────────────────────────────────────────── */
    const TAB_KEY = 'active_tab';

    function showTab(name) {
      document.querySelectorAll('.tab-panel').forEach(p => {
        p.classList.toggle('active', p.dataset.tab === name);
      });
      document.querySelectorAll('.tab-button').forEach(b => {
        b.classList.toggle('active', b.dataset.tab === name);
      });
      localStorage.setItem(TAB_KEY, name);
    }

    function initTabs() {
      document.querySelectorAll('.tab-button').forEach(b => {
        b.addEventListener('click', () => showTab(b.dataset.tab));
      });
      const saved = localStorage.getItem(TAB_KEY) || 'briefing';
      showTab(saved);
    }

    /* ── Reading Journal ──────────────────────────────────── */
    const JOURNAL_KEY = 'journal_entries';

    function loadJournal() {
      const s = localStorage.getItem(JOURNAL_KEY);
      if (!s) return {};
      try {
        const parsed = JSON.parse(s);
        return (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) ? parsed : {};
      } catch (e) {
        localStorage.removeItem(JOURNAL_KEY);
        return {};
      }
    }
    function saveJournal(map) { localStorage.setItem(JOURNAL_KEY, JSON.stringify(map)); }

    function formatJournalDate(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      if (isNaN(d)) return iso;
      return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    }

    let _journalSaveTimer = null;
    function setJournalStatus(text) {
      const status = document.getElementById('journal-save-status');
      if (status) status.textContent = text;
    }

    function scheduleJournalSave(value) {
      clearTimeout(_journalSaveTimer);
      setJournalStatus('Saving…');
      _journalSaveTimer = setTimeout(() => {
        const map = loadJournal();
        if (!value.trim()) {
          delete map[CURRENT_ESSAY.url];
        } else {
          map[CURRENT_ESSAY.url] = {
            url: CURRENT_ESSAY.url,
            title: CURRENT_ESSAY.title,
            note: value,
            date: new Date().toISOString(),
          };
        }
        saveJournal(map);
        setJournalStatus('Saved');
        renderPastEntries();
        setTimeout(() => {
          const status = document.getElementById('journal-save-status');
          if (status && status.textContent === 'Saved') status.textContent = '';
        }, 1500);
      }, 500);
    }

    function renderPastEntries() {
      const container = document.getElementById('journal-past-list');
      if (!container) return;
      const searchEl = document.getElementById('journal-search');
      const search = (searchEl?.value || '').trim().toLowerCase();
      const entries = Object.values(loadJournal())
        .sort((a, b) => (b.date || '').localeCompare(a.date || ''));
      const filtered = search
        ? entries.filter(e =>
            (e.title || '').toLowerCase().includes(search) ||
            formatJournalDate(e.date).toLowerCase().includes(search))
        : entries;
      if (!filtered.length) {
        container.innerHTML = '<div class="news-empty">' +
          (entries.length ? 'No matches.' : 'No saved notes yet.') + '</div>';
        return;
      }
      container.innerHTML = filtered.map(e => `
        <article class="journal-entry-card">
          <div class="journal-entry-header">
            <a href="${htmlEsc(e.url || '#')}" class="journal-entry-title" target="_blank" rel="noopener">${htmlEsc(e.title || 'Untitled')}</a>
            <span class="journal-entry-date">${htmlEsc(formatJournalDate(e.date))}</span>
          </div>
          <div class="journal-entry-note">${htmlEsc(e.note || '')}</div>
        </article>`).join('');
    }

    function initJournal() {
      const ta = document.getElementById('journal-textarea');
      if (!ta) return;
      const existing = loadJournal()[CURRENT_ESSAY.url];
      if (existing && existing.note) ta.value = existing.note;
      ta.addEventListener('input', () => scheduleJournalSave(ta.value));
      const search = document.getElementById('journal-search');
      if (search) search.addEventListener('input', renderPastEntries);
      renderPastEntries();
    }

    /* ── Init ─────────────────────────────────────────────── */
    function init() {
      initTheme();
      setGreeting();
      setDate();
      initTabs();

      const kwInput = document.getElementById('kw-input');
      const kwAddBtn = document.getElementById('kw-add-btn');
      if (kwAddBtn) kwAddBtn.addEventListener('click', addKeyword);
      if (kwInput) kwInput.addEventListener('keydown', e => { if (e.key === 'Enter') addKeyword(); });

      const wlInput = document.getElementById('wl-input');
      const wlAddBtn = document.getElementById('wl-add-btn');
      if (wlAddBtn) wlAddBtn.addEventListener('click', addCompany);
      if (wlInput) wlInput.addEventListener('keydown', e => { if (e.key === 'Enter') addCompany(); });

      renderKws();
      renderWatchlist();
      initJournal();
    }

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init);
    } else {
      init();
    }
"""


# ── HTML builder ────────────────────────────────────────────
def build_news_grid(feed_results):
    sources_map = {}
    for r in feed_results:
        sources_map.setdefault(r["config"]["source"], []).append(r)

    sections = []
    for source_name in SOURCE_ORDER:
        source_results = sources_map.get(source_name, [])
        if not source_results:
            continue
        cards = []
        for result in source_results:
            entries = result["entries"]
            error = result["error"]
            is_podcast = result["config"]["is_podcast"]
            tag = result["config"]["tag"]
            tag_class = result["config"]["tag_class"]

            if error:
                cards.append(f'<p class="msg error">Could not load: {escape(error)}</p>')
            elif not entries:
                msg = "No recent episodes." if is_podcast else "No new items in the last 36 hours."
                cards.append(f'<p class="msg">{msg}</p>')
            else:
                for entry in entries:
                    title = escape(clean_title(entry.get("title")))
                    link = escape(entry.get("link", "#"))
                    date_str = format_card_date(entry, is_podcast)
                    date_html = f'<span class="card-date">{escape(date_str)}</span>' if date_str else ""
                    cards.append(
                        f'<article class="item-card">'
                        f'<span class="tag {tag_class}">{escape(tag)}</span>'
                        f'<a href="{link}" class="card-title" target="_blank" rel="noopener">{title}</a>'
                        f'{date_html}'
                        f'</article>'
                    )
        body = "\n        ".join(cards)
        grid_class = SOURCE_GRID_CLASS.get(source_name, "")
        sections.append(
            f'      <section class="{grid_class}">\n'
            f'        <h2>{escape(source_name)}</h2>\n'
            f'        {body}\n'
            f'      </section>'
        )
    return "\n\n".join(sections)


def build_stocks(stocks):
    groups_html = []
    for group in STOCK_GROUPS:
        cards = []
        for ticker, name in group["tickers"]:
            data = stocks.get(ticker, {})
            price = data.get("price")
            pct = data.get("change_pct")
            price_str = format_price(ticker, price)
            change_str = format_change(pct)
            change_class = "up" if (pct is not None and pct >= 0) else "down"
            cards.append(
                f'        <div class="stock-card">'
                f'<span class="stock-ticker">{escape(ticker)}</span>'
                f'<span class="stock-name">{escape(name)}</span>'
                f'<span class="stock-price">{escape(price_str)}</span>'
                f'<span class="stock-change {change_class}">{escape(change_str)}</span>'
                f'</div>'
            )
        groups_html.append(
            f'      <p class="stock-group-label">{escape(group["label"])}</p>\n'
            f'      <div class="stock-row">\n'
            f'{chr(10).join(cards)}\n'
            f'      </div>'
        )
    return "\n".join(groups_html)


def build_html(feed_results, stocks, watchlist_news, quote, essay):
    today = datetime.now().strftime("%A, %B %-d, %Y")
    generated_at = datetime.now().strftime("%-I:%M %p")
    total_items = sum(len(r["entries"]) for r in feed_results)
    item_label = "items" if total_items != 1 else "item"

    grid_html = build_news_grid(feed_results)
    stocks_html = build_stocks(stocks)

    pg_title, pg_url = essay

    news_cache = {company.lower(): r["items"] for company, r in watchlist_news.items()}

    current_essay = {"title": pg_title, "url": pg_url}

    js_data = (
        f"    const DEFAULT_COMPANIES = {js_safe_json(DEFAULT_WATCHLIST)};\n"
        f"    const WATCHLIST_NEWS_CACHE = {js_safe_json(news_cache)};\n"
        f"    const CURRENT_ESSAY = {js_safe_json(current_essay)};\n"
    )

    quote_escaped = escape(quote)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Morning Briefing &middot; {escape(today)}</title>
  <style>{_CSS}  </style>
</head>
<body>
  <div class="wrap">

    <header>
      <p class="greeting" id="greeting">Good morning, Ella.</p>
      <h1 id="today-date">{escape(today)}</h1>
      <blockquote>
        <p>{quote_escaped}</p>
        <cite>&mdash; Emily Dickinson</cite>
      </blockquote>
      <button class="theme-toggle" onclick="toggleTheme()">Dark</button>
    </header>

    <nav class="tabs">
      <button class="tab-button" type="button" data-tab="briefing">Briefing</button>
      <button class="tab-button" type="button" data-tab="journal">Reading Journal</button>
    </nav>

    <div class="tab-panel" data-tab="briefing">
      <details class="kw-panel">
        <summary>Keywords <span id="kw-count"></span></summary>
        <div class="kw-panel-body">
          <div id="kw-chips" class="kw-chips"></div>
          <div class="kw-input-row">
            <input id="kw-input" class="kw-input" type="text"
                   placeholder="Add a keyword&hellip;" autocomplete="off" />
            <button id="kw-add-btn" class="kw-add" type="button">Add</button>
          </div>
        </div>
      </details>

      <div class="grid">
{grid_html}
      </div>

      <div class="stocks-section">
        <h2>Stocks</h2>
{stocks_html}
      </div>

      <div class="watchlist-section">
        <h2>Watchlist &mdash; Private Companies</h2>
        <div class="wl-add-row">
          <input id="wl-input" class="wl-input" type="text"
                 placeholder="Add a company&hellip;" autocomplete="off" />
          <button id="wl-add-btn" class="wl-add" type="button">Add</button>
        </div>
        <div id="watchlist-grid" class="watchlist-grid"></div>
      </div>
    </div>

    <div class="tab-panel" data-tab="journal">
      <div class="pg-bar">
        <span class="tag tag-essay">Today's Essay</span>
        <a href="{escape(pg_url)}" class="pg-link" target="_blank" rel="noopener">{escape(pg_title)}</a>
        <span class="pg-source">paulgraham.com</span>
      </div>

      <div class="journal-note-block">
        <div class="journal-note-label">
          <span>End your notes</span>
          <span class="journal-save-status" id="journal-save-status"></span>
        </div>
        <textarea id="journal-textarea" class="journal-textarea"
                  placeholder="Write your thoughts on this essay&hellip;"
                  spellcheck="true"></textarea>
      </div>

      <div class="journal-past-section">
        <h2>Past Entries</h2>
        <input id="journal-search" class="journal-search" type="text"
               placeholder="Search by essay title or date&hellip;" autocomplete="off" />
        <div id="journal-past-list"></div>
      </div>
    </div>

    <footer>Generated at {escape(generated_at)} &middot; {total_items} {item_label}</footer>
  </div>
  <script>
{js_data}{_JS_COMMON}  </script>
</body>
</html>
"""


# ── Local server (bypasses Google News CORS) ────────────────
SERVE_PORT = 8765


class _DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/news":
            self._handle_news_api(parsed)
            return
        if parsed.path == "/":
            self.path = "/morning_briefing.html"
        return super().do_GET()

    def end_headers(self):
        # Force the browser to refetch the dashboard each time so a tab left
        # open across days doesn't keep rendering stale generated HTML.
        if self.path == "/morning_briefing.html":
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def _handle_news_api(self, parsed):
        params = urllib.parse.parse_qs(parsed.query)
        q = (params.get("q", [""])[0] or "").strip()
        if not q:
            self._send_json({"error": "missing q"}, status=400)
            return
        result = fetch_company_news(q)
        self._send_json(result["items"])

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        sys.stderr.write(f"  [{self.address_string()}] {format % args}\n")


class _ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(port=SERVE_PORT):
    os.chdir(str(Path.home()))
    url = f"http://localhost:{port}/"
    with _ThreadingServer(("", port), _DashboardHandler) as httpd:
        print(f"\nServing dashboard at {url}")
        print("Open the page in your browser. Press Ctrl-C to stop.")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")


# ── Main ────────────────────────────────────────────────────
def main():
    print(f"Fetching {len(FEEDS)} RSS feeds, {sum(len(g['tickers']) for g in STOCK_GROUPS)} stocks, "
          f"and Google News for {len(DEFAULT_WATCHLIST)} watchlist companies...")

    with ThreadPoolExecutor(max_workers=3) as ex:
        feeds_future = ex.submit(fetch_all_feeds)
        stocks_future = ex.submit(fetch_all_stocks)
        watch_future = ex.submit(fetch_all_watchlist_news)

        feed_results = feeds_future.result()
        stocks = stocks_future.result()
        watchlist_news = watch_future.result()

    for r in feed_results:
        label = r["config"]["label"]
        if r["error"]:
            print(f"  x {label}: {r['error']}")
        else:
            print(f"  + {label}: {len(r['entries'])} items")

    for ticker, data in stocks.items():
        if data["error"]:
            print(f"  x {ticker}: {data['error']}")
        else:
            print(f"  + {ticker}: {format_price(ticker, data['price'])} ({format_change(data['change_pct'])})")

    for company, r in watchlist_news.items():
        if r["error"]:
            print(f"  x {company}: {r['error']}")
        else:
            print(f"  + {company}: {len(r['items'])} headlines")

    quote = pick_quote()
    essay = pick_essay()
    content = build_html(feed_results, stocks, watchlist_news, quote, essay)

    output = Path.home() / "morning_briefing.html"
    output.write_text(content, encoding="utf-8")

    print(f"\nSaved -> {output}")

    if "--serve" in sys.argv:
        serve()
    else:
        webbrowser.open(output.as_uri())


if __name__ == "__main__":
    main()
