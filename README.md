# Morning Briefing

A single-command, local-only morning dashboard. Fetches live RSS feeds,
public-equity prices, and Google News for a private-company watchlist,
then opens a dense, themed HTML page in the browser. Everything runs on
your machine — no accounts, no third-party services.

Includes a **Reading Journal** tab with auto-saving notes (keyed by
essay URL, stored in `localStorage`) for the daily Paul Graham essay.

For the full architecture writeup, design decisions, and per-component
deep dive, see [`yc_session_summary.md`](yc_session_summary.md).

## Install

```
pip install feedparser yfinance
```

Python 3.10+ recommended.

## Run

```
python3 news_dashboard.py            # fetch, render, open in browser
python3 news_dashboard.py --serve    # also start the local CORS proxy on :8765
```

Output is written to `~/morning_briefing.html`. `--serve` mode lets
user-added watchlist companies fetch news through the same Python pipeline
used at page-build time (query enrichment + topic-keyword filtering).

### Optional one-word alias

Add this to `~/.zshrc` so `briefing` in any terminal does everything:

```
alias briefing='python3 ~/news-dashboard/news_dashboard.py --serve'
```

## Customization

All knobs live as module-level constants near the top of
[`news_dashboard.py`](news_dashboard.py). Edit and re-run; no other steps.

| What you want to change | Edit this constant |
| --- | --- |
| News sources / RSS feeds | `FEEDS` |
| Layout placement of each source in the grid | `SOURCE_ORDER`, `SOURCE_GRID_CLASS`, plus `.grid-*` CSS in `_CSS` |
| How many podcast episodes per show | `PODCAST_EPISODE_LIMIT` |
| How far back to pull news articles | `NEWS_LOOKBACK_HOURS` |
| Public-equity tickers | `STOCK_GROUPS` |
| Default watchlist of private companies | `DEFAULT_WATCHLIST` |
| Google News query enrichment per company | `WATCHLIST_QUERIES` |
| Topic keywords used to filter watchlist results | `WATCHLIST_TOPICS` |
| Paul Graham essay rotation | `PG_ESSAYS` |
| Emily Dickinson quote rotation | `DICKINSON_QUOTES` |
| Greeting name (currently "Ella") | `setGreeting()` inside `_JS_COMMON` |
| Default keyword highlights (briefing tab) | `DEFAULT_KWS` inside `_JS_COMMON` |
| Colors, fonts, dark-mode palette | `:root` and `[data-theme="dark"]` blocks in `_CSS` |
| Local server port | `SERVE_PORT` |

### Adding a private company

Append it to `DEFAULT_WATCHLIST`. If the bare name is ambiguous (`Ro`,
`Whoop`, etc.), add an enriched query to `WATCHLIST_QUERIES` and a list
of topic keywords to `WATCHLIST_TOPICS`. Without those entries the company
still works — it just takes the top two raw Google News results.

### Browser-side state

All client preferences live in `localStorage` and persist across daily
regenerations:

| Key | What it holds |
| --- | --- |
| `theme` | `"light"` or `"dark"` |
| `active_tab` | `"briefing"` or `"journal"` |
| `kw_keywords` | array of highlight keywords |
| `watchlist_companies` | array of company names (overrides `DEFAULT_WATCHLIST`) |
| `journal_entries` | map of `essayUrl → { title, url, note, date }` |

Clearing site data resets everything to the defaults compiled into the
script.

## Dependencies

- `feedparser` — RSS parsing
- `yfinance` — equity prices
- Standard library only for the rest (`http.server`, `concurrent.futures`,
  `html`, `json`, `urllib.parse`, `webbrowser`).
