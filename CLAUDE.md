# PC Pricing Tracker: project memory

Reference for future sessions. The README is the full write-up for the reader of the assignment; this file is the short version plus everything that is *not* in the README: the user's decisions, what was tried and failed, and the traps. Figures are as of **21 Nov 2026**; recompute before quoting them.

## What this is

An assignment for Lenovo ("AI-powered PC Pricing Tracker", AI Application Development Intern). It tracks Best Buy prices of laptops with equivalent specs, keeps a daily history, and charts the trend per comparison group. Lenovo is the **anchor brand**: its laptops define the groups, other brands are matched to them.

Lenovo confirmed by email (16 Nov 2026): history built from daily snapshots is acceptable, and the three laptops in the brief are only an example, so groups must be computed from specs.

## What the user asked for, and decided

- Goal: **10 distinct groups with many competitors, ideally 5 or 6, minimum 2.** "The most important thing is the normaliser: similar specs should match automatically."
- Start from the brief's example group: HP OmniBook X Flip 14", Lenovo Yoga 16", Dell Plus 16", all Core Ultra 5 226V / 16 GB / 512 GB SSD / Arc 130V / Windows / convertible.
- **No Task Scheduler for now** (said 20 Nov). The daily job is run by hand. Do not register a scheduled task unless asked.
- **ShopSavvy credits are scarce**: ask before any paid call and state the cost.
- Dashboard layout was the user's call: the group-independent sections (collection methods, unmatched specs, review queue, runs) live on their own page, not inside a comparison group.
- The README was a draft the user said could be edited. `README(draft).md` is their original and is kept untouched locally; it is git-ignored and not on GitHub (user's call, 22 Nov). `README.md` is the current one; component docs are in `docs/`.
- The user writes briefly and with typos ("scalper" = scraper). Reply plainly and lead with the result.

## Current state

| | |
|---|---|
| Collected | 503 New, Best Buy-sold laptops, 12 brand entries = 10 makers, each with its full spec table |
| Normalised | 496 grouped; 7 held for review (`python build_groups.py --show review`) |
| Groups | 1,256 across four levels; 116 tracked; 174 laptops priced daily; 10 featured |
| Prices | 79,969 rows: `bestbuy_graphql` 851 (19, 20, 21 Nov), `honey` 5,727, `honey_carried` 73,391 (from 24 Apr 2026) |
| Supabase | Schema applied, fully in sync with the local store |
| Dashboard | Streamlit, three pages, reads Supabase |
| Tests | 212 pass, 1 skipped (the database test needs `TEST_DATABASE_URL`) |
| Normaliser version | `2026.11.4` |

Lenovo groups by number of other brands:

| Level | Groups with a Lenovo | ≥1 other brand | ≥2 | ≥3 | ≥4 | ≥5 |
|---|---|---|---|---|---|---|
| strict | 111 | 18 | 3 | 0 | 0 | 0 |
| exact | 93 | 25 | 6 | 0 | 0 | 0 |
| relaxed | 86 | 35 | 12 | 3 | 0 | 0 |
| class | 80 | 38 | 18 | 9 | 3 | 1 |

**The honest ceiling:** Best Buy sells no identical configuration from more than three other brands. Five competitors exists exactly once, at the class level. The user has been told; which level to present to Lenovo is still their decision.

## Running it

Windows, Python 3.13, venv at `.venv` (use `.venv\Scripts\python.exe`). Not a git repository.

```
python collect_top_laptops.py --all-brands     # crawl every brand in data/brands.json (55 requests)
python build_groups.py [--show featured|tracked|unmatched|review] [--level ...]
python add_to_collection.py <url-or-sku> ...   # add + pin; rebuilds groups itself
python get_prices_now.py [--all]               # the daily job; --all prices all 503, default the 174 tracked
python backfill_history.py [--all]             # Honey history (free)
python backfill_history.py --source shopsavvy --start ... --end ... [--yes]   # PAID, dry run without --yes
python -m tracker.db check|init|sync|pull
streamlit run app/streamlit_app.py             # http://localhost:8501
pytest
```

Every script writes the local store first, then syncs to Supabase by itself. `python -m tracker.db sync` replays everything and is idempotent.

## Layout

```
collect_top_laptops.py, add_to_collection.py, build_groups.py, get_prices_now.py, backfill_history.py
tracker/
  sources/bestbuy_graphql.py   primary collector: session, listing, spec tables, prices
  sources/honey.py             backfill + fallback
  sources/shopsavvy.py         paid backfill client (works; the data is not there)
  normalise.py                 raw specs + title -> typed fields, the four keys, labels   <- the core
  grouping.py                  levels, tracked + featured groups, harmonise_cpus
  collect.py                   steps shared by the scripts (snapshot rows, outlier flag, rebuild_groups)
  store.py                     LocalStore: JSON-lines files under data/
  db.py                        Supabase mirror: init, sync, pull, check
  dashboard.py                 data layer of the dashboard, no Streamlit in it
  config.py                    settings from .env
app/
  streamlit_app.py             entry point: st.navigation over the three pages
  app_pages/groups.py          Comparison groups
  app_pages/explorer.py        Spec explorer (filters for every spec)
  app_pages/data.py            Data and methods (4 tabs)
  common.py                    load_data, render_trend (filters, tiles, chart, table) shared by pages
  charts.py                    Plotly figures, brand colours
sql/schema.sql                 6 tables, 4 views, read-only policies; idempotent
tests/                         normalise, grouping, store, sources, dashboard, db integration
data/                          the local store + shopsavvy_raw/ (paid answers)
bestbuy-scalper/, bestbuy-price-history/   the user's earlier experiments; reference only, do not edit
```

## Design decisions and the reason for each

**Storage: local store first, Supabase as a mirror.** A day the scraper ran can never be lost to a database outage, and everything works offline. Run ids are epoch milliseconds made by the job so an offline run keeps its id.

**Only New offers sold by Best Buy (seller class `1P`) count.** Of about 1,950 Lenovo listings, about 130 are Best Buy's own; the rest are Marketplace, mostly reconfigured "Upgraded ... + Dock" machines. Marketplace and open-box prices are different offers. Honey has no Marketplace data either.

**The normaliser** (all of this came from profiling the real spec tables):

- The spec table is the source; the title is the fallback and a cross-check. Titles often omit the CPU model.
- **CPU identity = vendor + model number** (`intel-226v`, `amd-350`). Best Buy's "Processor Model" series field is wrong too often to use (a Ryzen AI 7 350 filed under "Ryzen 7 3000 Series"). The model-number field is free text (`Ultra 7 355`, `U7 355`, `IntelCore 7 350`, `X126100`) and is parsed per vendor.
- AMD PRO chips share the identity of the plain chip: "PRO" appears in some listings of a chip and not others.
- Integrated graphics collapse to `integrated`: the same iGPU is listed as "Intel Arc", "Intel Arc 130V" and "Intel Graphics", and Dell labels some "Dedicated". Only a discrete model is kept (`nvidia-rtx-5060`).
- Storage snaps to one size: 1 TB is listed as both 1000 and 1024 GB; both become 1024.
- The NVIDIA N1X is sold with 18 and 20 cores under one model number, $400 apart: the core count is part of that identity (`nvidia-n1x-18c`).
- Form factor: `clamshell | convertible | detachable | dual-screen`. "Yoga" alone is not a 2-in-1 signal. A 2-in-1 under 2 lb is a detachable. The 2-in-1 flag is missing on 157 listings, so title words decide there.
- A listing whose spec table and title name different CPUs or GPUs is held for review, not grouped on either.
- A key field that cannot be read stays `None` and the laptop is never grouped. Wrong groups are worse than missing ones.
- Screen size, colour and OS edition are not in the exact key (the brief's own example mixes 14" and 16").

**Four match levels**, each containing the one before:

| Level | Same... |
|---|---|
| strict | exact + screen size bucket (`≤13`, `14`, `15–16`, `17+`) |
| exact | CPU model, memory, storage size, storage type, form factor, touchscreen, OS, GPU |
| relaxed | exact minus storage type and touchscreen |
| class | relaxed with CPU class (family + tier + generation, e.g. `intel-core-ultra-7-s2`) instead of the model |

The class level was added because exact matching cannot reach the user's competitor target. It mixes U, V and H chips of one tier, so it is a market-tier comparison and is always labelled.

**Tracked** = has the anchor brand and at least `MIN_COMPETITORS` (1) other brands, at any level. **Featured** = 10 groups: pinned laptops' exact groups first, then one group per class-level family, each shown at the narrowest of class/relaxed/exact that keeps all its competitor brands. Strict is never featured. Sub-brands fold into their maker (HP OMEN → HP, Alienware → Dell).

**Dashboard conventions:** solid line with dots = read from Best Buy by the tracker; dashed = Honey backfill. Step lines, gaps for unobserved days. Colour follows the brand; the brand→colour mapping was chosen by measuring every colour pair and is fixed in `app/charts.py`. With under 7 first-hand days the backfill is on by default.

## Data sources: what is known

**Best Buy storefront GraphQL** (`https://www.bestbuy.com/gateway/graphql`), three operations: `PlpSearch` (listing by `brand_facet` + `condition_facet=New`, Marketplace off, 15 per page), `ProductDetails` and `PriceBatch` (aliased `productBySkuId`). Confirmed fields: `skuId brand bsin upc openBoxCondition name{short} seller{classification} price(input){customerPrice regularPrice} specificationGroups{name specifications{displayName value}} hierarchy{bbypres{...}}`.

- The gateway only answers a session whose cookies were earned by a real, **visible** Chrome (Playwright warm-up; headless is rejected). Cookies are cached in `.bestbuy_session.json`. So the collector cannot run headless or in a cloud runner.
- Pace is one request per second, two in flight. A refusal stops the run; nothing escalates.
- Listing `numFound` counts package bundles (`SearchCombo`) that carry no product: fewer SKUs than `numFound` is normal.
- In-stock status is not collected.

**Honey** (`d.joinhoney.com/v3`, no key): a price *change log* per Best Buy SKU, id derived from the SKU. Starts 24 Apr 2026. Had a record for 502 of 503 laptops. Its latest price matched the first-hand price for 176 of 179 on day one.

**ShopSavvy** (paid): the client works, the data is not there. Pilot on 20 Nov: 10 laptops, 71 credits, **0 price points** (4 UPCs unknown, 1 wrong product, 5 with empty history). 929 of 1,000 trial credits were left. History before 24 Apr 2026 has no source.

**The brief's Dell does not exist at Best Buy today.** No Dell Plus 2-in-1 with the Core Ultra 5 226V in Best Buy's listings (new, open-box, refurbished), in Marketplace, or in Honey. Dell sells that line with the Core Ultra 7 256V. The example group therefore holds Lenovo 6615769 + HP 6613868 (both pinned, so it is always featured #1) with one competitor. If the user finds the old Dell's SKU, `add_to_collection.py` pins it in.

## Supabase

Project ref `huptsgitckndvsoikicm`, PostgreSQL 17, eu-west-1. The connection string is in `.env` as `SUPABASE_DB_URL` (never print it or commit it).

- This machine has no IPv6, so the direct host `db.<ref>.supabase.co` does not resolve. The **session pooler** works: `aws-1-eu-west-1.pooler.supabase.com:5432`, user `postgres.<ref>` (`aws-0-...` refuses).
- Tables are readable by the `anon` role and not writable (RLS on, select-only policy). The user was told the data is publicly readable with the publishable key.
- The Supabase MCP server is in `.mcp.json` but was never approved; the direct connection is what is used. The ShopSavvy MCP server is added at local scope.

## Traps

- **Bash heredocs halve backslashes on this machine.** An inline `python - <<'EOF'` script that rewrote regexes silently wrote control characters into `normalise.py`. Change code containing backslashes with Write/Edit only.
- **Do not guess undocumented Best Buy GraphQL fields.** The permission check denied schema probing on 19 Nov. Work with the confirmed fields above.
- **The dashboard must be restarted after code changes to `tracker/` or new pages**; a browser refresh keeps the old modules in memory and shows an `AttributeError`.
- The dashboard takes about 8 seconds to load from Supabase.
- Background servers started from a session get killed when the machine is low on memory. The user runs the dashboard from their own terminal.
- Windows console is cp1252: group labels contain `·`; scripts reconfigure stdout to UTF-8.
- `db.sync` sends each snapshot column as one typed array (`unnest`), not COPY: psycopg refuses a list mixing `399` and `399.99`, so values are coerced per column.
- After changing normaliser rules: bump `NORMALISER_VERSION`, diff the output for all 503 laptops before and after, run `build_groups.py`, update the README counts.
- `project.zip` (about 194 MB) in the root is the user's own archive.

## Open items

1. Scheduling the daily job (user deferred it). A day without a run has no first-hand price.
2. `get_prices_now.py` without `--all` prices only the 174 tracked laptops; the explorer's other laptops get no new first-hand prices.
3. The normaliser audit is incomplete: three of five reviewer lenses returned and I verified their findings by hand; the reviews of the groups as a whole and of title-only Marketplace listings failed on an API certificate error. The separate code review of collector, store and database layer returned nothing for the same reason.
4. Which match level to present, and whether class-level groups are acceptable to Lenovo.
5. "Chassis" is interpreted as form factor + touchscreen; unconfirmed with Lenovo.
6. One price to check by hand: Dell Premium 14.5" (SKU 6636560) at $507.99 on 19 Nov, down from about $1,900.
7. No hand check of collected prices against live product pages has been done.
8. Whether the Supabase data should stay publicly readable.
