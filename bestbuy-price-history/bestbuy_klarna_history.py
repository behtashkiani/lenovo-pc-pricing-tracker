#!/usr/bin/env python3
"""
Daily Best Buy prices for the last ~3 months from Klarna's US price comparison (no key, no login).

Use as a cross-check for bestbuy_price_history.py (Honey), not as the main source:
  * Klarna matches products itself, so you pick a Klarna product by search text, not by Best Buy SKU.
  * Its "Best Buy" series can include third-party Marketplace sellers on bestbuy.com, so on days
    when Best Buy's own offer is at list price Klarna may show a lower, odd-looking price.
  * Without a Klarna login the history is capped at about 3 months.

Usage:
    python3 bestbuy_klarna_history.py "lenovo ideapad slim 3 15.6"        # list matching products
    python3 bestbuy_klarna_history.py "lenovo ideapad slim 3 15.6" --pick 1
    python3 bestbuy_klarna_history.py 3464702258                          # Klarna product ID
    options: --csv out.csv   --all-merchants   --changes-only
"""
import argparse
import csv
import json
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://www.klarna.com/us/api"
BEST_BUY_MERCHANT_ID = "252237"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


def get_json(url, tries=4):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Klarna request failed: {last}")


def search(text):
    url = f"{BASE}/instant-search-edge-rest/public/search/suggest/us?q={urllib.parse.quote(text)}"
    return get_json(url).get("products") or []


def price_history(product_id, merchant_id=BEST_BUY_MERCHANT_ID):
    url = (f"{BASE}/product-information-edge-rest/public/pricehistory/product/{product_id}/US/DAY"
           f"?merchantId={merchant_id}&selectedInterval=THREE_MONTHS&filter=NATIONAL")
    return get_json(url)


def main():
    ap = argparse.ArgumentParser(description="Best Buy daily prices from Klarna (about 3 months)")
    ap.add_argument("product", help="search text, or a numeric Klarna product ID")
    ap.add_argument("--pick", type=int, help="with search text: use the Nth match")
    ap.add_argument("--all-merchants", action="store_true", help="lowest price across all stores, not only Best Buy")
    ap.add_argument("--changes-only", action="store_true", help="print only the days the price changed")
    ap.add_argument("--csv", help="write the daily series to this CSV file")
    a = ap.parse_args()

    if a.product.isdigit():
        product_id, name = a.product, ""
    else:
        hits = search(a.product)
        if not hits:
            sys.exit("No Klarna products matched that text.")
        if a.pick is None:
            print("Matches (re-run with --pick N):")
            for i, p in enumerate(hits, 1):
                low = (p.get("lowestPrice") or {}).get("amount", "?")
                print(f"  {i:2}. id {p['id']}  from ${low:>8}  {p['name'][:90]}")
            sys.exit(0)
        product_id, name = hits[a.pick - 1]["id"], hits[a.pick - 1]["name"]

    data = price_history(product_id, "" if a.all_merchants else BEST_BUY_MERCHANT_ID)
    rows = [{"date": h["timestamp"][:10], "price": h["price"], "merchant": h.get("merchantName", "")}
            for h in data.get("history") or []]
    if not rows:
        sys.exit(f"Klarna has no {'price' if a.all_merchants else 'Best Buy'} history for product {product_id}. "
                 f"Best Buy may not list this product in Klarna's feed; try --all-merchants or another match.")

    print(f"Klarna product {product_id}  {name}")
    print(f"{len(rows)} days, {rows[0]['date']} to {rows[-1]['date']}: "
          f"low ${data.get('lowest')}, high ${data.get('highest')}")
    last = None
    for r in rows:
        if not a.changes_only or r["price"] != last:
            print(f"  {r['date']}  ${r['price']:>9.2f}  {r['merchant']}")
        last = r["price"]
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["date", "price", "merchant"])
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} rows to {a.csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
