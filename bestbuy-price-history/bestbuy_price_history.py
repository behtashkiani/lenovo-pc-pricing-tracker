#!/usr/bin/env python3
"""
Historical Best Buy prices for any Best Buy-sold product, without the Best Buy API.

Source: PayPal Honey's public product endpoint (no key, no login). Honey keeps a
price-change log per Best Buy SKU; its product ID is derived from the SKU:

    honey_product_id = "28_" + md5(md5(sku)) + "_" + md5(md5(sku))      (28 = Best Buy)

Usage:
    python3 bestbuy_price_history.py 6668500
    python3 bestbuy_price_history.py "https://www.bestbuy.com/site/.../6668500.p?skuId=6668500"
    python3 bestbuy_price_history.py "https://www.bestbuy.com/product/<slug>/<BSIN>"
    python3 bestbuy_price_history.py "lenovo ideapad slim 3 ryzen 5"        # search, pick with --pick N
    options: --days 120   --csv out.csv   --json   --pick N

Only the Python standard library is used.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request

API = "https://d.joinhoney.com/v3"
STORE_ID = "28"  # Best Buy US in Honey
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


def _md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def honey_id_from_sku(sku):
    h = _md5(_md5(str(sku).strip()))
    return f"{STORE_ID}_{h}_{h}"


def gql(operation, variables, tries=4):
    qs = urllib.parse.urlencode({"operationName": operation,
                                 "variables": json.dumps(variables, separators=(",", ":"))})
    req = urllib.request.Request(f"{API}?{qs}", headers={"User-Agent": UA, "service-name": "honey-website"})
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # network hiccup or throttling: back off and retry
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Honey request failed after {tries} tries: {last}")


def get_products(product_ids):
    d = gql("web_getProductByIds", {"productIds": list(product_ids), "meta": {}})
    return [p for p in ((d.get("data") or {}).get("getProductByIds") or []) if p]


def search(text, limit=40):
    d = gql("web_search_searchProduct", {"query": text, "meta": {"limit": limit, "offset": 0, "storeIds": STORE_ID}})
    return ((d.get("data") or {}).get("searchProduct") or {}).get("products") or []


def get_history(product_id, days=400):
    d = gql("web_getProductPriceHistory", {"productId": product_id, "timeframe": days})
    return (d.get("data") or {}).get("getProductPriceHistory") or {}


def resolve(arg, pick=None):
    """Return (honey_product_id, sku_or_None). Accepts a SKU, a bestbuy.com URL, or search text."""
    arg = arg.strip()
    if re.fullmatch(r"\d{5,9}", arg):
        return honey_id_from_sku(arg), arg
    if "bestbuy.com" in arg:
        m = (re.search(r"[?&]skuId=(\d+)", arg) or re.search(r"/sku/(\d+)", arg)
             or re.search(r"/(\d{5,9})\.p(?:[?/#]|$)", arg))
        if m:
            return honey_id_from_sku(m.group(1)), m.group(1)
        m = re.search(r"/product/([^/?#]+)/([A-Z0-9]{8,12})(?:[/?#]|$)", arg)
        if m:  # new-style URL with no SKU in it: find the product by its BSIN through search
            words, bsin = m.group(1).split("-"), m.group(2)
            for n in (10, 8, 6, 4):
                hits = search(" ".join(words[:n]))
                if not hits:
                    continue
                for p in get_products([h["productId"] for h in hits]):
                    if (p.get("canonicalUrl") or "").rstrip("/").endswith("/" + bsin):
                        return p["productId"], None
            sys.exit(f"Could not find BSIN {bsin} in Honey. Pass the numeric SKU instead "
                     f"(it is shown on the product page under 'SKU').")
        sys.exit("Could not read a SKU or BSIN from that URL.")
    hits = search(arg, limit=15)
    if not hits:
        sys.exit("No Best Buy products matched that search text.")
    if pick is None:
        print("Matches (re-run with --pick N):")
        for i, p in enumerate(hits, 1):
            print(f"  {i:2}. ${p.get('priceCurrent', 0) / 100:>9.2f}  {p.get('title', '')[:100]}")
        sys.exit(0)
    return hits[pick - 1]["productId"], None


def daily_series(history, product, days, today=None):
    """Turn Honey's change log into one row per day (the price holds until the next change)."""
    today = today or dt.date.today()
    start = today - dt.timedelta(days=days)
    points = [(dt.date.fromisoformat(p["day"]), p["lo"] / 100) for p in history.get("history") or []]
    prev = history.get("prevPoint") or {}
    if prev.get("day"):
        points.insert(0, (dt.date.fromisoformat(prev["day"]), prev["lo"] / 100))
    points.sort()
    last_seen = None
    if product and product.get("updated"):
        last_seen = dt.datetime.fromtimestamp(product["updated"] / 1000, dt.timezone.utc).date()
    rows, i, price = [], 0, None
    change_days = {d for d, _ in points}
    day = min(start, points[0][0]) if points else start
    while day <= today:
        while i < len(points) and points[i][0] <= day:
            price = points[i][1]
            i += 1
        if day >= start and price is not None:
            if day in change_days:
                basis = "observed"
            elif last_seen and day > last_seen:
                basis = "carried_past_last_check"
            else:
                basis = "carried_forward"
            rows.append({"date": day.isoformat(), "price": price, "basis": basis})
        day += dt.timedelta(days=1)
    return rows


def main():
    ap = argparse.ArgumentParser(description="Best Buy historical prices via Honey")
    ap.add_argument("product", help="SKU, bestbuy.com product URL, or search text")
    ap.add_argument("--days", type=int, default=120, help="how many days back to report (default 120)")
    ap.add_argument("--csv", help="write the daily series to this CSV file")
    ap.add_argument("--json", action="store_true", help="print raw result as JSON")
    ap.add_argument("--pick", type=int, help="with search text: use the Nth match")
    a = ap.parse_args()

    pid, sku = resolve(a.product, a.pick)
    products = get_products([pid])
    product = products[0] if products else None
    history = get_history(pid, max(a.days, 400))
    points = history.get("history") or []
    if not product and not points:
        sys.exit(f"Honey has no record for {'SKU ' + sku if sku else pid}. Honey only tracks items sold by "
                 f"Best Buy itself; third-party Marketplace SKUs (usually 8 digits) are not covered.")
    rows = daily_series(history, product, a.days)

    if a.json:
        print(json.dumps({"honeyProductId": pid, "sku": sku, "product": {k: product.get(k) for k in
              ("title", "canonicalUrl", "priceCurrent", "priceList", "updated", "availability")} if product else None,
              "changePoints": points, "daily": rows}, indent=1))
    else:
        if product:
            print(product.get("title", ""))
            print(product.get("canonicalUrl", ""))
            seen = dt.datetime.fromtimestamp(product["updated"] / 1000, dt.timezone.utc).date()
            print(f"Honey ID {pid}" + (f"  (SKU {sku})" if sku else ""))
            print(f"Latest: ${product.get('priceCurrent', 0) / 100:.2f}   list ${product.get('priceList', 0) / 100:.2f}"
                  f"   last checked by Honey {seen}")
        if points:
            print(f"\n{len(points)} recorded price points, {points[0]['day']} to {points[-1]['day']}:")
            for p in points:
                print(f"  {p['day']}  ${p['lo'] / 100:>9.2f}")
        if rows:
            prices = [r["price"] for r in rows]
            print(f"\nLast {a.days} days ({rows[0]['date']} to {rows[-1]['date']}): "
                  f"low ${min(prices):.2f}, high ${max(prices):.2f}, {len(rows)} daily rows")
            if dt.date.fromisoformat(rows[0]["date"]) > dt.date.today() - dt.timedelta(days=a.days):
                print("Note: Honey's record for this product starts later than the requested window.")
    if a.csv and rows:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["date", "price", "basis"])
            w.writeheader()
            w.writerows(rows)
        print(f"\nWrote {len(rows)} rows to {a.csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
