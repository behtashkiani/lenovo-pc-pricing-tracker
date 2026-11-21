# Testing

```
pytest                 # 212 pass, 1 skipped, about 2 seconds
```

No test touches the network. The one skipped test needs a database (below).

## The suite

| File | Tests | What it pins down |
|---|---|---|
| `tests/test_normalise.py` | 168 (26 functions, most of them tables) | Real Best Buy titles and spec strings with their expected values: every spelling of a CPU model found in the data, listings that must stay unparsed, title numbers that are not CPU models, graphics, capacities, OS, form factor, touchscreen, model names, labels |
| `tests/test_grouping.py` | 12 | The brief's three laptops form one tracked group; near-misses stay out; the levels nest; sub-brands fold; Marketplace and open-box offers do not count; unparsed laptops are never grouped; featured groups are ranked, distinct and as narrow as possible; a pinned laptop's group comes first; the anchor is configurable |
| `tests/test_store.py` | 5 | Merge rules, the sticky pin, price-row uniqueness, three-strike delisting, the tracked set |
| `tests/test_sources.py` | 14 | Parsing a gateway payload, condition inference, SKU and BSIN from URLs, the outlier flag, Honey's id and its change log to daily rows, ShopSavvy windows, credit estimate, offer filter and row building |
| `tests/test_dashboard.py` | 13 | Source priority per day, own-only mode, gaps staying gaps, lowest price per brand, days at current price, the summary table, the explorer's laptop list and filters |
| `tests/test_db_integration.py` | 1 (skipped by default) | Schema, sync, views and pull against a real PostgreSQL |

`tests/conftest.py` provides `make_product(title, brand, cpu_brand=..., number=..., ram=..., ...)`, which builds a product with a Best Buy style spec table. Use it for new cases.

### Adding a normaliser case

Take the title and spec strings from the real listing, not from memory, and add a row to the matching table:

```python
("Intel", "Intel Core Ultra 7 Series 1", "Ultra 7 355",
 'Lenovo - ThinkPad T14 G7 14" Copilot+ PC - Latest Model - Intel Ultra7 with 32GB Memory - 512GB SSD - Black',
 "intel-355", "Intel Core Ultra 7 355", "intel-core-ultra-7-s3"),
```

Check that the expected value is right, not only that the test passes: these tables are the definition of correct.

## The database test

Skipped unless `TEST_DATABASE_URL` points at a **disposable** PostgreSQL. It drops and recreates the tracker's tables, so never point it at the real project.

```
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54330/postgres pytest tests/test_db_integration.py
```

A throwaway server without installing PostgreSQL (needs Node):

```
npm install @electric-sql/pglite @electric-sql/pglite-socket
node node_modules/@electric-sql/pglite-socket/dist/scripts/server.js --port=54330
```

Add `?sslmode=disable` to the URL for it. It accepts one connection at a time and does not support `COPY` over the wire, which is one reason the sync does not use `COPY`.

The test applies the schema twice, syncs twice (the second must add nothing), checks that the `tracked_skus` view equals the locally computed set, checks source priority in `daily_prices` and that a flagged row is never served, and pulls the data back into a fresh local store.

## Testing the dashboard

Streamlit's own harness runs the app in-process, without a browser:

```python
from streamlit.testing.v1 import AppTest
at = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
at.run()
assert not at.exception
at.sidebar.radio[0].set_value(at.sidebar.radio[0].options[1]).run()     # another featured group
at.switch_page("app_pages/explorer.py").run()
```

This catches exceptions and wrong values. It does not show layout: for that, run the app and look at it. There is no automated dashboard test in the suite; the checks during development were scripts of this kind plus screenshots.

## What has been verified, and what has not

Verified:

- **The normaliser on the full collection.** A before/after diff over all 503 laptops accompanies every rule change.
- **An audit of the normaliser** by three independent reviewer agents (CPU; memory, storage and graphics; chassis, OS and descriptive fields), each asked to prove defects from the raw listing. Their findings were checked by hand against the data before anything was changed. It led to the N1X core-count split, the dual-screen form factor, the weight rule for detachables, and several guards.
- **The database layer** against a local PostgreSQL with the full store, and then against the Supabase project: a repeat sync adds nothing, the SQL tracked set equals the local one, the `anon` role can read and cannot write.
- **The daily job live**: 179 of 179 priced in 9 requests; a second run added no rows.
- **Honey against first-hand prices**: equal for 176 of 179 laptops on the first day.
- **The ShopSavvy client live**: one request, behaving as documented (and returning no history).

Not verified:

- Two parts of the normaliser audit did not complete: a review of the groups as a whole, and of title-only (Marketplace) listings.
- An independent code review of the collector, store and database layer did not complete.
- No sample of collected prices has been checked by hand against the live product pages.
- The strict match level was added after the audit.
