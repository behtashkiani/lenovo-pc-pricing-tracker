# Price history sources

Best Buy exposes no price history. Anything before the tracker's first run has to come from somewhere else, and is stored under its own label.

Code: `tracker/sources/honey.py`, `tracker/sources/shopsavvy.py`, `backfill_history.py`.

## Source labels and priority

| Label | Meaning | Rank |
|---|---|---|
| `bestbuy_graphql` | Read from Best Buy by this tracker | 1 |
| `honey` | A price change Honey recorded on that day | 2 |
| `honey_carried` | Honey's last recorded price, carried forward to a later day | 3 |
| `shopsavvy` | A price ShopSavvy observed on that day | 4 |
| `shopsavvy_carried` | ShopSavvy's last observed price, carried forward | 5 |

Every source keeps its own row for the same laptop and day. When a merged series is wanted, the lowest rank present wins. The ranking lives in two places that must agree: the `daily_prices` view in `sql/schema.sql` and `PRIORITY` in `tracker/dashboard.py`.

## Honey

PayPal Honey keeps a dated price-change log per Best Buy SKU and serves it from a public endpoint with no key or login.

```
honey_product_id = "28_" + md5(md5(sku)) + "_" + md5(md5(sku))        # 28 = Best Buy US

GET https://d.joinhoney.com/v3
    ?operationName=web_getProductPriceHistory
    &variables={"productId":"<id>","timeframe":400}
```

| Operation | Returns |
|---|---|
| `web_getProductPriceHistory` | `history[]` of `{day, lo, hi}` in cents, plus `prevPoint` (the last change before the window) |
| `web_getProductByIds` | Per product: title, canonical URL, `priceCurrent`, `priceList`, `updated` (when Honey last checked) |

The id is derived from the SKU, so there is no search and no fuzzy matching.

**It is a change log, not a daily sample.** A point is written when the price changes and the price holds until the next point. `honey.daily_rows` expands it:

- a day with a recorded change becomes a `honey` row,
- the days between changes become `honey_carried` rows,
- nothing is written after the date Honey last checked the product (or, if that is unknown, after the last change). A price is never carried into days nobody observed.

`lo` is used as the price. `list_price` and `in_stock` are left empty.

Facts measured on this project's data:

- Records start on **24 April 2026**.
- Honey had a record for 502 of the 503 collected laptops.
- It covers only offers sold by Best Buy. Marketplace SKUs are absent.
- On 19 Nov 2026 Honey's latest price equalled the first-hand price for 176 of 179 laptops; the three differences were changes of that day or the day before.
- Against archived Best Buy pages (Common Crawl), 95% of 589 compared observations matched exactly on the same day (see the README, section 12).

It is unofficial and undocumented, which is why it is a backup and not the primary source.

### Commands

```
python backfill_history.py                    # every tracked laptop
python backfill_history.py --all              # every collected laptop (needed for the spec explorer)
python backfill_history.py --sku 6615769      # these only
python backfill_history.py --days 200         # shorter window; default 400
```

Requests are sequential with a 0.6 s pause. Re-running is safe: existing rows are skipped.

### As a fallback

When the daily run cannot price a SKU, it reads Honey for the last 7 days for that SKU. The rows land on their true dates under the Honey labels, so nothing is presented as a first-hand reading.

## ShopSavvy

A paid, documented API (`https://api.shopsavvy.com/v1`). The client was written from ShopSavvy's OpenAPI specification and works as documented.

**The data is not there.** A pilot on 20 Nov 2026 asked for 10 tracked laptops that were on sale before Honey's log starts, for 25 Mar to 24 May 2026:

| Result | Laptops |
|---|---|
| UPC unknown to ShopSavvy | 4 |
| UPC matched a different product | 1 |
| Matched, Best Buy offer found, history empty | 5 |
| Price points returned | 0 |

It cost 71 credits. ShopSavvy's Best Buy offers for these laptops were all last seen between September and November 2026, a period Honey already covers. It is not used for this project's history, and history before 24 April 2026 has no source.

### How the client works

```
GET /v1/products/offers/history?ids=<upc,...>&start=YYYY-MM-DD&end=YYYY-MM-DD&retailer=bestbuy.com
Authorization: Bearer $SHOPSAVVY_API_KEY
```

- Laptops are looked up by **UPC**, up to 100 per request. Results are mapped back through each product's `match.identifier`, never by position, because unmatched identifiers are simply absent.
- A window is at most 365 days; longer ranges are split.
- Only a first-party New offer is used: `condition == "new"`, `is_marketplace` not true, no `seller`, and the offer URL must carry the tracked SKU (or ShopSavvy's `identifiers.bestbuy` must equal it).
- Points arrive newest first. The last observation of each UTC day becomes a `shopsavvy` row; days between observations become `shopsavvy_carried`; nothing is carried past the last observation. Points with no currency are ignored.
- A 404 means none of the identifiers matched and costs nothing.
- **Every paid answer is saved** to `data/shopsavvy_raw/` before it is parsed, so a parsing mistake never costs a second request.

### Cost

| Item | Credits |
|---|---|
| Per product that has offers (with `retailer` set) | 2 |
| Per day of the window, once per request | 1 |
| `/usage` | 0 |
| A request with no offers, or any error | 0 |

So 100 laptops over 90 days in one request is 290 credits; as 100 separate requests it would be 9,200. The client always batches.

### Commands

```
python backfill_history.py --source shopsavvy --start 2026-03-25 --end 2026-05-24 --limit 10
python backfill_history.py --source shopsavvy --start ... --end ... --limit 10 --yes
```

Without `--yes` the command prints the worst-case cost and the credits remaining, and requests nothing. It refuses to run if the balance is below the estimate unless `--force` is given.

**Credits are scarce** (a 1,000-credit trial; 929 were left after the pilot). Do not spend any without the owner's agreement.

## Research scripts, not part of the tracker

`bestbuy-price-history/` holds the scripts used to choose and validate the backup source:

- `bestbuy_price_history.py`: the original Honey script the module was ported from.
- `bestbuy_klarna_history.py`: Klarna's price comparison. Rejected: its product matching is approximate and its "Best Buy" series mixes in Marketplace sellers.
- `bestbuy_commoncrawl_snapshots.py`: archived Best Buy pages from Common Crawl. Too sparse for a series, but it is Best Buy's own page, so it served as ground truth for validating Honey.

## Adding a source

1. Write a module in `tracker/sources/` that returns rows shaped like `{"sku", "snapshot_date", "price", "list_price", "in_stock", "source", "captured_at", "run_id"}`.
2. Choose its label or labels. Use a separate label for carried-forward days.
3. Give it a rank in the `daily_prices` view and in `dashboard.PRIORITY`, and a display name in `dashboard.SOURCE_LABEL` and `dashboard.METHOD`.
4. Wire it into `backfill_history.py`.
5. Validate it against first-hand prices before trusting it.
