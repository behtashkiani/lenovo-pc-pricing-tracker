# Documentation

Component documentation for the PC Pricing Tracker. The top-level [README](../README.md) is the write-up of the assignment (what was asked, the methodology, the results, the limitations). These pages describe how each part works and how to run or change it.

| Page | Covers | Code |
|---|---|---|
| [Architecture](architecture.md) | The pipeline end to end, the modules, the principles the design follows | whole repository |
| [Collector](collector.md) | Getting laptops, spec tables and prices from Best Buy | `tracker/sources/bestbuy_graphql.py`, `tracker/collect.py`, `collect_top_laptops.py`, `add_to_collection.py` |
| [Price history sources](price-history-sources.md) | Honey backfill and fallback, the ShopSavvy client, source priority | `tracker/sources/honey.py`, `tracker/sources/shopsavvy.py`, `backfill_history.py` |
| [Normalisation](normalisation.md) | Turning raw spec tables and titles into comparable fields | `tracker/normalise.py` |
| [Grouping](grouping.md) | The four match levels, tracked and featured groups | `tracker/grouping.py`, `build_groups.py` |
| [Storage](storage.md) | The local store, the Supabase schema and views, syncing, access | `tracker/store.py`, `tracker/db.py`, `sql/schema.sql` |
| [Operations](operations.md) | Setup, configuration, the daily run, routine tasks, troubleshooting | the five scripts, `.env` |
| [Dashboard](dashboard.md) | The three pages, the chart conventions, the data layer | `app/`, `tracker/dashboard.py` |
| [Testing](testing.md) | The test suite, the database test, what has and has not been verified | `tests/` |

## Reading order

- **To run it:** [Operations](operations.md).
- **To understand the matching:** [Normalisation](normalisation.md), then [Grouping](grouping.md).
- **To change something:** [Architecture](architecture.md) first, then the page for that part.

## Terms

| Term | Meaning |
|---|---|
| SKU | Best Buy's numeric id for one offer of a product. The key for everything price-related. |
| 1P / 3P | Sold by Best Buy itself / by a Marketplace seller. Only 1P counts. |
| Anchor brand | The brand whose laptops define the comparison groups. Lenovo by default (`ANCHOR_BRAND`). |
| Spec table | The roughly 150 rows of specifications Best Buy lists per product, stored untouched as `raw_specs`. |
| Group | Laptops whose normalised specs are identical at a given match level. |
| Match level | How much has to be identical: strict, exact, relaxed or class. |
| Tracked | A group with the anchor brand and at least one other brand. Its laptops are priced daily. |
| Featured | The ten headline groups the dashboard opens with. |
| Pinned | A laptop added by URL or SKU: always priced, and its exact group is always featured. |
| First-hand price | A price this tracker read from Best Buy (`source = bestbuy_graphql`). |
| Backfill | Earlier prices from a third-party log, stored under their own source label. |
| Local store | The JSON-lines files under `data/`. Written first; Supabase mirrors it. |
