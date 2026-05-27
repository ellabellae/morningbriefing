# Morning Briefing Dashboard — Session Summary
# Session conducted using Claude Code (Anthropic), full session built from scratch in one sitting, 3,312 lines of code

## What the project is

A local, single-command morning briefing tool. Running `briefing` in any
terminal fetches live data from a half-dozen RSS feeds, ten public-equity
tickers, and Google News for a configurable list of private companies, and
opens a dense, fully-themed HTML dashboard in the browser. Everything runs on
the user's machine; no third-party services, no accounts.

The deliverable is two files plus a shell alias:

- `news_dashboard.py` — fetches all data, generates HTML, optionally serves
  it with a local CORS-bypass proxy.
- `morning_briefing_sample.html` — static design preview used to iterate on
  layout without re-fetching data.
- `~/.zshrc` alias `briefing` → `python3 ~/news_dashboard.py --serve`.

---

## Key features

### Content
- **News grid** (3-column × 2-row CSS Grid, internal scroll per cell):
  Endpoints News (pharma / R&D / health-tech sub-feeds, merged into one
  full-height left column), Fierce Biotech (top-right span), Huberman Lab
  (bottom-middle), Acquired (bottom-right).
- **Stocks**: real prices and 1-day percent change via `yfinance`, grouped
  into Market & Tech (`^GSPC`, `AAPL`, `NVDA`) and Pharma & Health (`MRK`,
  `JNJ`, `NVO`, `PFE`, `LLY`, `HIMS`, `OSCR`). Color-coded up/down.
- **Watchlist** of private companies (Oura, Anthropic, OpenAI, Whoop, Ro,
  Pomelo Care) with the two most recent Google News headlines per company,
  query-enriched and topic-filtered server-side.
- **Paul Graham essay of the day**, deterministically rotated by day-of-year
  through a curated list of 12 essays.
- **Emily Dickinson quote of the day**, same rotation pattern over 20 quotes.
- **Personalized greeting** (Good morning / afternoon / evening, Ella) set
  client-side from the local clock.

### Interaction
- **Dark mode toggle**, persisted in `localStorage`.
- **Keyword manager**: collapsible panel; add/remove tags with localStorage
  persistence; matches highlight (`<mark>`) across every article title in the
  page in real time.
- **Watchlist manager**: text input + add button; each card has an X to
  remove; new companies trigger a live news fetch through the local proxy on
  add. Default list is pre-populated; user changes override via localStorage.

---

## Technical architecture

### Data layer (Python)

```
            ThreadPoolExecutor
                    │
   ┌────────────────┼────────────────┐
   │                │                │
fetch_all_feeds  fetch_all_stocks  fetch_all_watchlist_news
   │                │                │
feedparser       yfinance         feedparser
   │              .Ticker          (Google News
   │              .history          RSS)
   │                │                │
   └────────────────┴────────────────┘
                    │
              build_html()
                    │
            ~/morning_briefing.html
```

All three fetch families run concurrently; within `fetch_all_feeds` and
`fetch_all_watchlist_news` there is a second tier of parallelism so a slow
feed never blocks the others. Total wall time is dominated by the slowest
single response, not the sum.

### Rendering layer

`build_html()` returns a single self-contained HTML string. Inline CSS
(`_CSS`) and JS (`_JS_COMMON`) live as module-level Python strings so the
outer f-string doesn't have to escape `{` and `}`. `_JS_COMMON` is a raw
string (`r"""…"""`) so regex backslashes survive verbatim.

Server-fetched data is injected as a JS data prelude:

```js
const DEFAULT_COMPANIES = [...];
const WATCHLIST_NEWS_CACHE = { "oura": [...], ... };
```

…then concatenated with `_JS_COMMON`. The cache is keyed by lowercased name
so `loadCompanyNewsInto()` can do an O(1) lookup before falling back to a
network request.

### Client layer

Vanilla JS, no frameworks. Three independent state stores in `localStorage`:

| Key                      | Shape                  | Owner            |
| ------------------------ | ---------------------- | ---------------- |
| `theme`                  | `"light" \| "dark"`    | dark-mode toggle |
| `kw_keywords`            | `string[]`             | keyword manager  |
| `watchlist_companies`    | `string[]`             | watchlist manager|

`renderKws()` and `renderWatchlist()` are idempotent: each one reads from
storage, wipes its container, and rebuilds the DOM. Add/remove handlers
mutate storage and call the renderer again.

### Optional local server

`python3 news_dashboard.py --serve` starts a `ThreadingHTTPServer` on port
8765 that:

1. Serves `morning_briefing.html` at `/`.
2. Exposes `GET /api/news?q=<company>` which calls the same Python
   `fetch_company_news()` used at page-build time — so user-added companies
   inherit the same query enrichment and topic filtering as the defaults.

The JS tries `/api/news` first and falls back to the direct
`news.google.com` URL if the proxy is missing, so the same generated HTML
works in both modes.

---

## Interesting engineering decisions

### 1. CORS bypass via a thin Python proxy
The browser refused to fetch `news.google.com/rss/search` directly — Google
doesn't return permissive CORS headers, so neither `file://` nor
`http://localhost` worked. Rather than ship the user to a third-party CORS
proxy, the dashboard's own Python script doubles as the proxy when invoked
with `--serve`. Crucially, the proxy delegates to the *same*
`fetch_company_news()` already used during page generation, so the
client-side "add a company" path automatically gets query enrichment and
topic-keyword filtering for free. One implementation, two call sites.

### 2. Two-stage relevance filter for ambiguous company names
Bare-name searches for short, common words returned garbage — `Ro` returned
articles about Ro Khanna and Crocs collabs. The fix has two layers:

- **Query enrichment** (`WATCHLIST_QUERIES` map): each default company has a
  hand-tuned query, e.g. `"Ro" telehealth pharmacy weight loss`.
- **Topic-keyword post-filter** (`WATCHLIST_TOPICS` map): each headline's
  title + summary is scored against a sector vocabulary; items with score
  zero are dropped. Top 25 candidates are pulled so the filter has room to
  work without starving the result set.

User-added companies skip the filter (no entry in the maps) and just take
the top two — cheap to extend per-company later if needed.

### 3. Server-render defaults, client-fetch additions, single render path
The watchlist is rendered the same way regardless of source. Defaults are
embedded in `WATCHLIST_NEWS_CACHE` at page-build time so they paint
instantly with no network round-trip. Newly-added companies fall through to
`fetchCompanyNews()`, which itself prefers the local proxy and falls back to
direct fetch. Three sources, one render function, one cache key namespace.

### 4. Title cleaning for feeds that ship HTML in `<title>`
Fierce Biotech's RSS literally embeds `<a href="…">Headline</a>` inside the
`<title>` element. Naively running it through `html.escape()` made the
angle brackets render as visible text. A small `clean_title()` helper
strips tags with a regex and `html.unescape()`s entities before the title
is passed to the template.

### 5. Defensive `localStorage` reads
The first version of `loadCompanies()` and `loadKws()` did
`JSON.parse(localStorage.getItem(...))` directly. Any corrupted value (from
a stale browser profile, an interrupted write, manual editing) would throw,
abort the surrounding init script, and leave the page dead — including
breaking the Add buttons. The fix wraps both reads in `try/catch` plus an
`Array.isArray()` check, and self-heals by deleting the bad key. This was
the actual root cause behind a "nothing happens when I click Add" bug
reported mid-session.

### 6. `addEventListener` over inline `onclick`
Inline `onclick="addCompany()"` requires `addCompany` to be globally
visible at attribute-evaluation time. That's normally fine, but combined
with the bug above it created a silent failure mode: any earlier exception
in the script left the function defined-but-unreachable from the global
scope in a way that was hard to diagnose. Switching to
`addEventListener` inside an `init()` function (gated on
`DOMContentLoaded`) makes the wiring explicit and fail-loud.

### 7. Sample HTML as a design iteration target
`morning_briefing_sample.html` is a hand-written copy of the live template
with hardcoded sample data. It exists so layout/CSS work doesn't have to
wait on real RSS fetches and so design changes can be reviewed in isolation
before being ported into `news_dashboard.py`'s `_CSS` constant. The two
files share no code at runtime, but the sample is the canonical reference
for what the live page should look like.

### 8. Concurrent fetches with bounded parallelism
The top-level `main()` runs feeds, stocks, and watchlist concurrently. Each
of those internally uses its own `ThreadPoolExecutor` sized to the number
of items (`max_workers=len(FEEDS)` etc.), so a single slow source doesn't
hold up the others. Total cold-start time is ~5–8 seconds end to end on
typical home networks.

### 9. `js_safe_json` for safe `<script>` embedding
Embedding JSON-encoded RSS titles inside a `<script>` tag is fine until a
title contains the literal substring `</script>`. The helper replaces
`</` with `<\/` in the JSON output — invisible to `JSON.parse()` but
preventing a stray closing tag from breaking out of the script context.

### 10. Single shell alias as the user-facing API
The whole project ultimately collapses to one word in any terminal:
`briefing`. The alias points at `python3 ~/news_dashboard.py --serve`,
which fetches, renders, serves, and opens the browser. Ctrl-C tears the
whole thing down. No daemons, no config files, no PATH changes.

---

## File map

| Path                                     | Role                                        |
| ---------------------------------------- | ------------------------------------------- |
| `~/news_dashboard.py`                    | data layer, HTML generator, optional server |
| `~/morning_briefing.html`                | generated artifact (regenerated each run)   |
| `~/morning_briefing_sample.html`         | static design preview                       |
| `~/.zshrc` (alias `briefing`)            | one-command entry point                     |

## Dependencies

- Python 3.13 (system, via Anaconda)
- `feedparser` — RSS parsing
- `yfinance` — equity data
- Standard library only for the rest (`http.server`, `socketserver`,
  `urllib.parse`, `concurrent.futures`, `html`, `json`, `re`,
  `webbrowser`).
