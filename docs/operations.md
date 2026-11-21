# Operations

How to set the tracker up, run it, and deal with what goes wrong.

## Requirements

- Python 3.11 or newer (built on 3.13, Windows 11).
- Google Chrome, for the one-off session warm-up. Playwright drives the installed Chrome; no browser download is needed.
- A machine with a desktop: the warm-up browser has to be visible.
- A Supabase project (optional; everything works on the local store without it).

## Setup

```
python -m venv .venv
.venv\Scripts\activate                 # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements.txt
copy .env.example .env                 # then fill it in
```

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `SUPABASE_DB_URL` | unset | Postgres connection string (session pooler). Unset means local store only. See [Storage](storage.md) |
| `ANCHOR_BRAND` | `Lenovo` | Brand whose laptops define the groups |
| `REQUEST_DELAY_SECONDS` | `1.0` | Minimum pause between requests to Best Buy |
| `MIN_COMPETITORS` | `1` | Other brands a group needs to be tracked |
| `FEATURED_COUNT` | `10` | Number of featured groups |
| `FEATURED_MIN_COMPETITORS` | `2` | Other brands a group needs to be featured |
| `SHOPSAVVY_API_KEY` | unset | Enables the paid ShopSavvy backfill. Not needed |
| `TRACKER_DATA_DIR` | `./data` | Where the local store lives |
| `BESTBUY_GRAPHQL_URL` | the gateway | Override the endpoint |

`.env` is in `.gitignore`. It holds the database password and the API key: never commit or print it.

## First run

```
python collect_top_laptops.py --all-brands     # about 55 requests; Chrome opens once
python build_groups.py                         # normalise, group, print the summary
python backfill_history.py --all               # Honey history for every laptop; several minutes
python -m tracker.db init                      # once, if using Supabase
python -m tracker.db sync
streamlit run app/streamlit_app.py
```

## The daily run

```
python get_prices_now.py
```

What it does:

1. Loads the tracked SKUs (members of tracked groups, plus pinned laptops).
2. Fetches prices from Best Buy, 20 SKUs per request, up to three attempts.
3. Stores each price as a `bestbuy_graphql` row for today (UTC).
4. Flags any price below 30% or above 300% of that laptop's 30-day median. A flagged price is stored and kept out of the charts.
5. For SKUs it could not price, reads Honey's log for the last 7 days.
6. Counts "no such SKU" answers; three in a row marks a laptop inactive.
7. Logs the run and syncs to Supabase.

| Option | Effect |
|---|---|
| `--all` | Price every collected laptop (about 26 requests), not only the tracked 174. Needed if the spec explorer should keep gaining first-hand prices for the others |
| `--sku A B ...` | Price only these |
| `--no-fallback` | Do not read Honey for failures |

| Exit code | Meaning |
|---|---|
| 0 | Everything priced |
| 1 | Nothing is tracked yet |
| 3 | Partial or blocked: some or all SKUs not priced |

Properties:

- **Idempotent.** A second run on the same day adds no rows.
- **A failed SKU does not fail the run.**
- **A missed day cannot be recovered first-hand.** Honey may fill it later, under its own label.
- The tracked set takes 9 requests and a few seconds.

**Scheduling is not set up.** The run is started by hand. Because the gateway needs a visible browser session, the job has to run on a desktop machine while someone is logged in; a scheduled task on that machine is the natural fit when wanted.

## Routine tasks

| Task | Command |
|---|---|
| Pick up newly listed laptops | `python collect_top_laptops.py --all-brands`, then `python build_groups.py`, then `python backfill_history.py --all` |
| Add a brand | Add `{"name": "<Best Buy's spelling>", "enabled": true}` to `data/brands.json`, then `python collect_top_laptops.py --brand <name>`, then `build_groups.py` |
| Add one laptop | `python add_to_collection.py <url-or-sku>` |
| Apply changed normalisation rules | `python build_groups.py` |
| See what could not be matched | `python build_groups.py --show review` and `--show unmatched` |
| Look at the groups from another brand's side | `python build_groups.py --anchor HP` (this rewrites the stored groups; run it again without `--anchor` afterwards) |
| Check the database | `python -m tracker.db check` |
| Catch the database up | `python -m tracker.db sync` |

## Troubleshooting

**Chrome opens during a run.** Normal. The cached session was rejected or missing, and the collector is earning a new one. Leave the window alone; it closes itself.

**`BLOCKED: ...`** The gateway refused the client and the run stopped. Wait and try again later, or pass `--fresh` to force a new session. If warm-ups keep failing, copy a Cookie header from a real bestbuy.com tab's DevTools into a file and pass `--cookie-file`. Do not raise the request rate.

**`no browser session was accepted by the gateway`.** Five warm-up attempts failed. Check that Chrome is installed and that `--headless` is not set. Retry later.

**`cannot connect` / `getaddrinfo failed` on a database command.** The connection string is the "Direct connection" one. Use the Session pooler string; see [Storage](storage.md).

**`database sync failed`** at the end of a script. The data is safe in the local store. Fix the connection and run `python -m tracker.db sync`.

**`database not configured`.** `SUPABASE_DB_URL` is empty or still has the placeholder. Expected if you are working locally.

**The dashboard shows an `AttributeError` or an old page after a code change.** Stop it (Ctrl+C) and start it again. A browser refresh keeps the old modules in memory.

**The dashboard takes several seconds to open.** It is loading about 80,000 price rows from Supabase; the result is cached for ten minutes.

**A group lost a laptop.** Check `python build_groups.py --show review`: a rule change, or a listing that now contradicts itself, may have moved it to review. Also check `active` in `data/products.jsonl`: three "no such SKU" answers mark a laptop inactive.

**A price looks wrong on a chart.** Open the laptop's Best Buy link from the table and compare. First-hand prices are what Best Buy returned at that moment; dashed lines are Honey. The sanity flag needs three earlier first-hand days before it can catch an outlier.

**Garbled `·` or `–` in the terminal.** The Windows console is cp1252. The scripts set UTF-8 output themselves; when piping to a file, set `PYTHONIOENCODING=utf-8`.

**ShopSavvy.** `backfill_history.py --source shopsavvy` without `--yes` only prints the cost. Read [Price history sources](price-history-sources.md) before spending credits: the pilot returned nothing.

## Files that must stay private

| File | Why |
|---|---|
| `.env` | Database password, API key |
| `.bestbuy_session.json` | Session cookies |
| `bestbuy-scalper/cookie.txt`, `bestbuy-scalper/bestbuy.sh` | A pasted browser cookie from early testing |

All are in `.gitignore`. The project is not a git repository at the time of writing; keep the ignore file if it becomes one.
