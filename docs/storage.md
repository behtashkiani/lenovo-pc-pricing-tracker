# Storage

Two copies of the data: a local store that every script writes first, and a Supabase Postgres database that mirrors it.

Code: `tracker/store.py`, `tracker/db.py`, `sql/schema.sql`. Tests: `tests/test_store.py`, `tests/test_db_integration.py`.

## Why two

- A day the collector ran can never be lost to a database outage: the price is already on disk.
- Everything works before the database is connected, and in tests.
- The database can be rebuilt at any time by replaying the local store, and the local store can be rebuilt from the database on a new machine.

The dashboard reads the database when it is configured and reachable, and the local store otherwise.

## The local store

`LocalStore` over the files in `data/` (or `TRACKER_DATA_DIR`). The folder is in `.gitignore`.

| File | Content | Written by |
|---|---|---|
| `brands.json` | The brands table, maintained by hand | you |
| `products.jsonl` | One collected SKU per line, with its untouched spec table | collect, add |
| `product_specs.jsonl` | Normalised fields and group keys, one line per SKU | `build_groups.py` (rewritten in full) |
| `spec_groups.json` | Every group at every level, with its `skus` | `build_groups.py` (rewritten in full) |
| `price_snapshots.jsonl` | Append-only prices | collect, daily run, backfill |
| `scrape_runs.jsonl` | Append-only run log | every collecting job |
| `shopsavvy_raw/*.json` | Every paid ShopSavvy answer, as received | ShopSavvy backfill |

Rules the store enforces:

- **Products merge, they are not replaced.** `first_seen` is kept, `last_seen` moves, `pinned` is sticky, and a listing-only refresh (no spec table) never wipes a stored spec table.
- **A price row is unique on `(sku, snapshot_date, source)`.** `insert_snapshots` skips anything already present, so history cannot be rewritten and every job can be re-run.
- **Delisting needs three strikes.** `record_misses` counts consecutive "no such SKU" answers per laptop and sets `active = false` at three. A successful price resets the count. A blocked run never counts.
- Whole-file writes go through a temporary file and a rename.

## The database

PostgreSQL 17 on Supabase. The schema is one idempotent file, `sql/schema.sql`: run it as often as you like.

### Tables

| Table | Key | Content |
|---|---|---|
| `brands` | `name` | The brands table |
| `products` | `sku` | One SKU: `bsin`, `upc`, `url`, `title`, `brand`, `seller_class`, `condition`, `categories`, `raw_specs` (jsonb), `rank`, `first_seen`, `last_seen`, `pinned`, `active`, `miss_count` |
| `product_specs` | `sku` | The normalised fields (see [Normalisation](normalisation.md)), `screen_bucket`, the four keys `strict_key`, `group_key`, `relaxed_key`, `class_key`, `needs_review`, `review_notes`, `normaliser_ver` |
| `spec_groups` | `group_key` | One group at one `level`: `label`, the compared fields, `member_count`, `brand_count`, `competitor_count`, `brands`, `has_anchor`, `tracked`, `featured_rank` |
| `price_snapshots` | `id`; unique `(sku, snapshot_date, source)` | `price`, `list_price`, `in_stock`, `source`, `flagged`, `captured_at`, `run_id` |
| `scrape_runs` | `id` | `started_at`, `finished_at`, `source`, `kind`, `products_ok`, `products_failed`, `status`, `error` |

Notes:

- `snapshot_date` is the UTC date of the observation.
- `flagged` marks a first-hand price that failed the sanity check (below 30% or above 300% of the laptop's 30-day median). It is stored, and left out of every view and chart.
- Run ids are made by the job (epoch milliseconds), so a run logged offline keeps its id when it is replayed.
- `spec_groups` is generated and rewritten in full on every sync. Nothing references a group key by foreign key.
- In `spec_groups`, a field the level ignores is `null` (for example `touchscreen` on a relaxed group).

### Views

| View | Gives |
|---|---|
| `prices_own` | First-hand prices only (`bestbuy_graphql`, not flagged) |
| `daily_prices` | One price per laptop per day, taking the most trusted source (see [Price history sources](price-history-sources.md)) |
| `group_members` | `(group_key, level, sku)` for every group at every level; only New, Best Buy-sold, active laptops |
| `tracked_skus` | What the daily job prices: members of tracked groups, plus pinned laptops |

Example: the merged price series of the first featured group.

```sql
select p.brand, d.sku, d.snapshot_date, d.price, d.source
from spec_groups g
join group_members m on m.group_key = g.group_key
join daily_prices d on d.sku = m.sku
join products p on p.sku = d.sku
where g.featured_rank = 1
order by d.sku, d.snapshot_date;
```

### Access

- The tracker connects with the database password as `postgres`, which bypasses row level security.
- The schema turns row level security **on** for all six tables, revokes everything from the Data API roles `anon` and `authenticated`, and grants them `select` only, with a read-only policy. The views use `security_invoker`, so they obey the same rules.
- Result, checked by switching to the `anon` role: every table and view can be read, an insert is refused.
- **The data is publicly readable** by anyone holding the project's publishable key. That is deliberate, so a dashboard can read it. Tighten the policies in `schema.sql` if it should be private.

## Connecting

Put the connection string in `.env` as `SUPABASE_DB_URL`. Use the **Session pooler** string from the Supabase dashboard (the Connect button):

```
postgresql://postgres.<project-ref>:<password>@aws-<n>-<region>.pooler.supabase.com:5432/postgres
```

- The "Direct connection" host `db.<project-ref>.supabase.co` is IPv6-only on the free plan. It does not resolve on a network without IPv6, which includes the machine this was built on.
- Percent-encode reserved characters in the password (`#` is `%23`, `)` is `%29`).
- `sslmode=require` is added automatically for Supabase hosts.
- Prepared statements are disabled, so the transaction pooler (port 6543) works as well.
- An unset variable, or one still holding the `[YOUR-DATABASE-PASSWORD]` placeholder, means "no database": every script then works on the local store only.

## Commands

```
python -m tracker.db check     # can we connect, and what is in there
python -m tracker.db init      # apply sql/schema.sql
python -m tracker.db sync      # push the whole local store
python -m tracker.db pull      # rebuild the local store from the database
```

Every script ends with `db.sync_safely(...)` for the tables it touched. A database problem there prints a warning and never fails the run.

### How sync works

| Table | Method |
|---|---|
| `brands`, `products`, `product_specs`, `scrape_runs` | Upsert by key. `first_seen` keeps the earlier value; a missing spec table never overwrites a stored one; spec rows for products that no longer exist are deleted |
| `spec_groups` | Delete all, insert all, in the same transaction |
| `price_snapshots` | Insert with `on conflict do nothing` |

Prices are sent efficiently: the database is first asked how many rows it holds per `(source, day)`, and only slices where the local store has more are uploaded. A daily sync therefore sends one day of prices. Rows travel as one array per column in a single statement per 5,000 rows, which needs no `COPY` and works through either pooler mode.

The full store (503 products with spec tables, 1,256 groups, about 80,000 price rows) syncs in a few seconds; a repeat sync changes nothing.

### Moving to a new machine

```
python -m tracker.db pull      # brands, products, prices, run log
python build_groups.py         # regenerates specs and groups
```

## Changing the schema

1. Edit the `create table` statement **and** add an `alter table ... add column if not exists ...` below it, so existing databases are upgraded. The file must stay safe to run repeatedly.
2. Add the column to the matching list in `tracker/db.py` (`_PRODUCT_COLS`, `_SPEC_COLS`, `_GROUP_COLS`, `_RUN_COLS` or `_SNAPSHOT_COLS`).
3. Recreating a view with a different column list needs a `drop view` first; `create or replace view` can only add columns at the end.
4. Run `python -m tracker.db init`, then `sync`, then the database test (see [Testing](testing.md)).
