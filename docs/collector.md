# Collector

Gets laptops, their spec tables and their prices from Best Buy.

Code: `tracker/sources/bestbuy_graphql.py` (the client), `tracker/collect.py` (the steps), `collect_top_laptops.py` and `add_to_collection.py` (the commands). The daily price run uses the same client; see [Operations](operations.md).

## Source

The collector calls the GraphQL gateway the bestbuy.com pages themselves use: `https://www.bestbuy.com/gateway/graphql` (overridable with `BESTBUY_GRAPHQL_URL`). It is not a published API. Best Buy can change it without notice.

Three operations:

| Operation | Query | Batch | Used for |
|---|---|---|---|
| `PlpSearch` | `search(...)` on category "All Laptops" (`pcmcat138500050001`), sorted Best-Selling | 15 products per page, up to 100 pages | Discovery |
| `ProductDetails` | aliased `productBySkuId` | 10 SKUs per request | Spec tables |
| `PriceBatch` | aliased `productBySkuId` | 20 SKUs per request | Daily prices |

Fields requested (all confirmed to exist):

```
skuId brand bsin upc openBoxCondition
name { short }
seller { classification }                       # 1P or 3P
price(input: $in) { customerPrice regularPrice }
hierarchy { bbypres { primary id categoryDetail { name } } }
specificationGroups { name specifications { displayName value } }
```

`customerPrice` is stored as `price`, `regularPrice` as `list_price`. Stock status is not requested.

**Do not guess at other field names.** The gateway hides validation messages, and probing it for undocumented fields was stopped by the permission check during development. Work with the fields above.

## Discovery

`BestBuyGQL.discover(brand, condition="new", marketplace=False, limit=None)` filters the listing with facets:

- `brand_facet` = the brand exactly as Best Buy spells it (`ASUS`, `GIGABYTE`, `HP OMEN`)
- `condition_facet` = `New`
- `enableMarketplace: false`, so only offers sold by Best Buy are returned
- `collapse: false`, so every colour variant is its own result

Things to know:

- **`numFound` is larger than the number of SKUs returned.** The listing mixes in package bundles (`SearchCombo` entries) with no single product behind them. They are skipped and counted in the log line.
- **A result set over 1,450 is split by facet** (brand, price range, memory, screen size, storage, processor brand), because the listing stops answering after page 100. This only matters with `--include-marketplace`.
- **`rank`** records the position in Best Buy's best-selling order; `--limit N` takes the first N.
- **Condition** comes from the facet when a listing is filtered by it. For a laptop added by SKU it is inferred from the title and `openBoxCondition` (`infer_condition`).

## Session

The gateway only answers a session whose Akamai cookies were earned by a real browser running the site's scripts. `run_with_session(job)` handles this:

1. Use a pasted cookie header (`--cookie-file`), else the cached cookies in `.bestbuy_session.json`, else warm up.
2. **Warm-up** (`browser_cookies`): open Chrome through Playwright, load bestbuy.com, move the pointer a little, then make a probe request to the gateway from inside the page. Only a session the gateway answers is accepted; otherwise a fresh browser context is tried, up to five times.
3. Hand the cookies to `curl_cffi`, which does all the actual requests with Chrome's TLS and HTTP/2 fingerprint.
4. After a successful job, save the (refreshed) cookies back to the cache.
5. If the cached session is rejected, drop it, warm up once, and **re-enter the job**. Jobs are written to resume, not restart.
6. Any other refusal raises `Blocked`. The run stops. Nothing escalates.

Consequences:

- The warm-up browser must be **visible**. Headless sessions reach the same cookie state but are rejected by the gateway. `--headless` exists and currently does not work.
- The collector therefore needs a machine with a desktop. It cannot run in a cloud runner.
- `.bestbuy_session.json` is a login-cookie-like file. It is in `.gitignore`.

## Pacing and errors

| Setting | Value | Where |
|---|---|---|
| Minimum interval between request starts | `REQUEST_DELAY_SECONDS`, default 1.0 s, with up to 30% jitter | `.env` |
| Requests in flight | 2 | `CONCURRENCY` |
| Retries on a transient error | 4, with growing backoff | `MAX_RETRIES` |
| Timeout | 30 s | `TIMEOUT` |

| Response | Treated as |
|---|---|
| 200 with JSON | Success |
| 200 without JSON | `Blocked` (a challenge page) |
| 400 | An error in the query; raised |
| 403, or an HTTP/2 stream reset | `Blocked` |
| Connection failure every time | `Blocked` |

A connection timeout is indistinguishable from a silent refusal, so repeated timeouts also count as `Blocked` and trigger one re-warm-up.

## Commands

```
python collect_top_laptops.py --all-brands          # every enabled brand in data/brands.json, anchor first
python collect_top_laptops.py --brand hp
python collect_top_laptops.py --brand hp --limit 40 # the 40 best-selling only
```

| Option | Effect |
|---|---|
| `--condition new\|open-box\|refurbished\|any` | Default `new`. Other conditions are stored but never counted in a group |
| `--include-marketplace` | Also collect third-party offers. Never tracked |
| `--refresh-specs` | Fetch spec tables again for products already stored |
| `--fresh` | Ignore the cached session |
| `--cookie-file FILE` | Use a Cookie header copied from the browser's DevTools |

A crawl fetches spec tables only for SKUs that have none yet, records the price it saw as a first-hand snapshot, logs the run, and syncs. The full collection of 12 brand entries and 503 laptops took 55 requests.

```
python add_to_collection.py 6615769
python add_to_collection.py "https://www.bestbuy.com/site/6613868.p?skuId=6613868"
python add_to_collection.py "https://www.bestbuy.com/product/<slug>/<BSIN>/sku/12789225"
```

Adds single laptops through the same path and **pins** them (`--no-pin` to skip). A pinned laptop is always priced and its exact group is always featured. The command rebuilds the groups itself and prints where the laptop landed.

A new-style link without a SKU (`/product/<slug>/<BSIN>`) is resolved only if that BSIN is already in the collection. Otherwise pass the SKU shown on the product page.

## The brands table

`data/brands.json` is maintained by hand: a list of `{"name": ..., "enabled": true}`. Names must match Best Buy's facet spelling. It holds Lenovo, HP, Dell, ASUS, Acer, Microsoft, Samsung, MSI, LG, GIGABYTE, and Best Buy's separate sub-brand entries Alienware and HP OMEN, which the normaliser folds into Dell and HP.

Apple is left out deliberately: no Lenovo laptop runs macOS, so no group could contain both.

## What a stored product looks like

```json
{"sku": "6615769", "title": "Lenovo - Yoga 7i 2-in-1 - Copilot+ PC - 16\" 2K Touchscreen Laptop - ...",
 "brand": "Lenovo", "bsin": "...", "upc": "198155420948", "url": "https://www.bestbuy.com/site/6615769.p?skuId=6615769",
 "seller_class": "1P", "condition": "new", "open_box_condition": null, "categories": ["Windows Laptops", "All Laptops"],
 "rank": 6, "pinned": true, "active": true, "first_seen": "...", "last_seen": "...",
 "raw_specs": {"Processor": {"Processor Brand": "Intel", "Processor Model Number": "226V", ...},
               "Memory": {"System Memory (RAM)": "16 gigabytes", ...}, ...}}
```

`raw_specs` is `{group name: {display name: value}}`, exactly as listed. The normaliser reads from it; see [Normalisation](normalisation.md).
