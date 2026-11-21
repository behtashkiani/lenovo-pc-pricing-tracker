"""Backup source A: PayPal Honey's public price log (no key, no login).

Honey keeps a dated price-change log per Best Buy SKU. Its product id is derived
from the SKU, so no search or fuzzy matching is involved:

    honey_product_id = "28_" + md5(md5(sku)) + "_" + md5(md5(sku))        # 28 = Best Buy US

Properties that shape how it is used:

* It is a change log, not a daily sample: a point is written when the price
  changes and the price holds until the next point. `daily_rows` expands it to one
  row per day, labelling real change points `honey` and the days in between
  `honey_carried`, and stops at the date Honey last checked the product.
* It only covers offers sold by Best Buy itself (1P). Marketplace SKUs are absent.
* It is unofficial and undocumented, which is why it is a backup and not the
  primary source. Requests are sequential with a pause between them.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
import urllib.parse
import urllib.request

SOURCE = "honey"
SOURCE_CARRIED = "honey_carried"

API = "https://d.joinhoney.com/v3"
STORE_ID = "28"  # Best Buy US
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
PRODUCT_BATCH = 20


def honey_id(sku: str) -> str:
    h = hashlib.md5(hashlib.md5(str(sku).strip().encode()).hexdigest().encode()).hexdigest()
    return f"{STORE_ID}_{h}_{h}"


def _gql(operation: str, variables: dict, tries: int = 4) -> dict:
    qs = urllib.parse.urlencode({"operationName": operation,
                                 "variables": json.dumps(variables, separators=(",", ":"))})
    req = urllib.request.Request(f"{API}?{qs}", headers={"User-Agent": UA, "service-name": "honey-website"})
    last: Exception | None = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # network hiccup or throttling: back off and retry
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Honey request failed after {tries} tries: {last}")


def get_history(sku: str, days: int = 400) -> dict:
    """Raw change log: {'history': [{'day', 'lo', 'hi'}...] in cents, 'prevPoint': {...}}."""
    d = _gql("web_getProductPriceHistory", {"productId": honey_id(sku), "timeframe": days})
    return (d.get("data") or {}).get("getProductPriceHistory") or {}


def get_products(skus: list[str]) -> dict[str, dict]:
    """Honey's product record per SKU (title, canonicalUrl, priceCurrent, priceList, updated)."""
    by_id = {honey_id(s): str(s) for s in skus}
    out: dict[str, dict] = {}
    ids = list(by_id)
    for i in range(0, len(ids), PRODUCT_BATCH):
        d = _gql("web_getProductByIds", {"productIds": ids[i:i + PRODUCT_BATCH], "meta": {}})
        for p in (d.get("data") or {}).get("getProductByIds") or []:
            if p and p.get("productId") in by_id:
                out[by_id[p["productId"]]] = p
    return out


def last_checked(product: dict | None) -> dt.date | None:
    """The date Honey last looked at the product (its `updated` field, in ms)."""
    if product and product.get("updated"):
        return dt.datetime.fromtimestamp(product["updated"] / 1000, dt.timezone.utc).date()
    return None


def daily_rows(sku: str, history: dict, product: dict | None, *, days: int = 400,
               today: dt.date | None = None, run_id: int | None = None) -> list[dict]:
    """Expand Honey's change log into one price row per day.

    Change points become `honey` rows, the days between them `honey_carried` rows.
    Nothing is written after the date Honey last checked the product (or, when that
    is unknown, after the last change point): a price is never carried into days
    nobody observed.
    """
    today = today or dt.datetime.now(dt.timezone.utc).date()
    start = today - dt.timedelta(days=days)
    points: dict[dt.date, float] = {}
    for p in [history.get("prevPoint") or {}] + list(history.get("history") or []):
        if p.get("day") and p.get("lo") is not None:
            points[dt.date.fromisoformat(p["day"])] = p["lo"] / 100
    if not points:
        return []
    ordered = sorted(points.items())
    end = min(last_checked(product) or ordered[-1][0], today)

    rows, i, price = [], 0, None
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    day = ordered[0][0]
    while day <= end:
        while i < len(ordered) and ordered[i][0] <= day:
            price = ordered[i][1]
            i += 1
        if day >= start and price is not None:
            rows.append({"sku": str(sku), "snapshot_date": day.isoformat(), "price": price,
                         "list_price": None, "in_stock": None,
                         "source": SOURCE if day in points else SOURCE_CARRIED,
                         "captured_at": stamp, "run_id": run_id})
        day += dt.timedelta(days=1)
    return rows


def backfill(skus: list[str], *, days: int = 400, delay: float = 0.6, run_id: int | None = None,
             progress=None) -> tuple[list[dict], list[str]]:
    """History rows for many SKUs. Returns (rows, SKUs Honey has no record of)."""
    products = get_products(skus)
    rows: list[dict] = []
    unknown: list[str] = []
    for n, sku in enumerate(skus, start=1):
        try:
            history = get_history(sku, days)
        except RuntimeError as e:
            unknown.append(sku)
            if progress:
                progress(f"  [honey] {sku}: {e}")
            continue
        new = daily_rows(sku, history, products.get(sku), days=days, run_id=run_id)
        if new:
            rows.extend(new)
        else:
            unknown.append(sku)
        if progress and (n % 25 == 0 or n == len(skus)):
            progress(f"  [honey] {n}/{len(skus)} SKUs, {len(rows)} daily rows")
        time.sleep(delay)
    return rows, unknown
