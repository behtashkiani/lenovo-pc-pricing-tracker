# Dashboard

A Streamlit app with Plotly charts, in three pages.

Code: `app/streamlit_app.py` (navigation), `app/app_pages/` (the pages), `app/common.py` (what the pages share), `app/charts.py` (figures and colours), `tracker/dashboard.py` (the data layer). Tests: `tests/test_dashboard.py`.

```
streamlit run app/streamlit_app.py        # http://localhost:8501
```

It reads Supabase when `SUPABASE_DB_URL` is configured and reachable, and the local store otherwise. The sidebar footer of every page says which, and when prices were last collected. Loaded data is cached for ten minutes and reloaded when the local files change.

**After changing code under `tracker/` or adding a page, restart the app.** A browser refresh re-runs the page script but keeps imported modules in memory.

## Pages

Switched from the bar at the top.

### Comparison groups (`app_pages/groups.py`)

The groups the tracker computed: Lenovo against the brands with matching specs.

- **Sidebar.** *Featured* lists the ten featured groups with their level and competitor brands. *All tracked* adds a match-level control (strict, exact, relaxed, class) and a dropdown of every tracked group at that level, most competitors first.
- **Header.** The group's spec label, the brands compared, and a sentence on what its match level guarantees and what may differ.
- Then the price-trend section described below.

### Spec explorer (`app_pages/explorer.py`)

Every collected laptop that normalised fully (496), filtered by any combination of specs. The price-trend section redraws for whatever matches.

| Filter | Control |
|---|---|
| Brand, processor family, processor model, graphics | Multi-select dropdown |
| Memory, storage | Range slider over the sizes that exist |
| Screen size, price now | Range slider |
| Storage type, form factor, operating system | Buttons, several allowed |
| Touchscreen | Any, touch, non-touch |

An empty selection accepts everything; filters combine with "and". A range left at its full width is no constraint, so laptops with no screen size or price on file stay in until the range is narrowed. *Reset filters* clears all of it. The heading spells out the active filters and the count.

Laptops outside the tracked groups only have Honey history unless the daily job is run with `--all`.

### Data and methods (`app_pages/data.py`)

Everything that does not depend on a group, in four tabs:

| Tab | Shows |
|---|---|
| Collection methods | Price rows, laptops and date range per method; and for any one laptop, one line per method, never merged |
| Unmatched Lenovo specs | Lenovo configurations with no identical configuration from another brand, with laptop count and lowest price |
| Review queue | Laptops the normaliser could not place, with the reason |
| Runs | The last 30 runs |

## The price-trend section

`common.render_trend(data, skus, key=..., level=..., brand_filter=..., count_label=...)` draws the same block on the first two pages:

1. **Controls**, in one row above everything they scope: period (30, 90, 180 days, all), prices (own scraper only, or own scraper + backfill), and brand pills.
2. **Four headline figures**: Lenovo's lowest price here and its change in 30 days; the cheapest competitor and which laptop it is; the gap between the two; laptops and brands shown.
3. **The trend chart**, in one of two views:
   - *Lowest price per brand*: one step line per brand, labelled at its right end with the brand and current price. Hovering shows every brand's price that day, which laptop it was, and where the price came from.
   - *Every laptop*: one step line per laptop, coloured by brand. Above 40 laptops it falls back to the per-brand view and says so.
4. **The table**: per laptop, the specs its match level ignores, price now, a trend sparkline, discount from list price, lowest and highest in the period, when it was last at its lowest, days at the current price, resolution, and a link to Best Buy.

The table's spec columns follow the level (`dashboard.differing_features`), since only what a level ignores can differ inside a group:

| Level | Spec columns |
|---|---|
| strict | none |
| exact | screen size |
| relaxed | storage type, touchscreen, screen size |
| class | CPU, storage type, touchscreen, screen size |
| no group (explorer) | CPU, memory, storage, storage type, form factor, touchscreen, screen size |

## Chart conventions

- **Solid line with dots: a price this tracker read from Best Buy. Dashed line: backfill from Honey.** A hairline marks the first day of first-hand prices.
- **Step lines.** A price holds until it changes.
- **A day nobody observed is a gap**, never an interpolated value.
- **Colour identifies the brand and follows it.** Filtering does not repaint the brands that remain. The mapping is fixed in `charts.BRAND_SLOT`.
- **Never colour alone.** Every brand line is also labelled at its end, listed in the legend and named in the hover. Eight hues cannot all be told apart pairwise, so the colours were assigned by measuring every pair and putting the closest hues on brands that rarely share a group.
- **Default price mode.** Own scraper only, once there are seven first-hand days. Until then the backfill is on by default and the page says so, because one or two dots show no trend.
- A flagged price never appears.
- Light and dark themes both work; the palette has a separate set of steps for each.

## The data layer (`tracker/dashboard.py`)

No Streamlit in this module, so it is testable on its own.

| Function | Does |
|---|---|
| `load(prefer="auto")` | Returns a `Data` object from the database or the local store |
| `select_prices(prices, skus, mode)` | One row per laptop and day: `"own"` for first-hand only, `"merged"` for the most trusted source of each day |
| `to_daily(rows)` | Price and source tables, one row per calendar day, gaps left empty |
| `lowest_per_brand(price, source, brands)` | Per brand and day: lowest price, which laptop, which source |
| `latest_prices(price)` | Current price per laptop, ignoring laptops not priced in the last 3 days |
| `summarise(...)` | The rows of the table |
| `spec_table(data)` / `filter_specs(table, ...)` | The explorer's laptop list and its filtering |
| `differing_features(level)` | Which spec columns a level's table needs |
| `source_coverage(prices)` | The coverage table |
| `brand_of`, `short_names` | Maker per laptop; short unique display names |

`Data` holds `products`, `specs`, `groups` (with `skus` and `brands` as lists), `prices` (`sku, date, price, list_price, source, flagged`) and `runs`, as pandas frames, the same shape from either origin.

## Adding a page

1. Create `app/app_pages/<name>.py` as a plain script (no wrapping function). Start with:

   ```python
   import streamlit as st
   from common import data_footer, get_data
   from tracker import dashboard as dd

   data = get_data()
   with st.sidebar:
       st.header("...")
       st.divider()
       data_footer(data)
   ```
2. Register it in `app/streamlit_app.py` with `st.Page("app_pages/<name>.py", title=..., icon=":material/...:", help=...)`.
3. Give every widget a `key` with a prefix unique to the page (`g-`, `x-`, `d-` are taken).
4. Put calculations in `tracker/dashboard.py` and test them there; keep the page to layout.
5. Restart the app.

## Adding a brand colour

`charts.BRAND_SLOT` maps the eight most common makers to palette slots. A maker not in it takes a slot nobody in the same group uses, and falls back to neutral grey past eight. To change the mapping, check the pairs that will share a chart for distinguishability first; yellow, aqua and magenta are the faintest on a white background.
