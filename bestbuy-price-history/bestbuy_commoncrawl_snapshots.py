#!/usr/bin/env python3
"""
Ground-truth Best Buy prices from archived copies of bestbuy.com product pages in Common Crawl.

Common Crawl saves the real Best Buy page HTML about once a month per page (when it crawls it
at all). Each saved page carries the price in its JSON-LD block. Coverage is sparse (zero to a
few captures per product per month) but it is Best Buy's own page, it includes third-party
Marketplace offers, and it goes back further than Honey's record. Use it to verify or extend
the Honey series from bestbuy_price_history.py.

Usage:
    python3 bestbuy_commoncrawl_snapshots.py "https://www.bestbuy.com/product/<slug>/<BSIN>"
    options: --crawls 6 (how many monthly crawls back)   --cache-dir DIR   --csv out.csv

The first run downloads one ~100 MB index file per crawl into the cache directory.
It reads Common Crawl's index files straight from data.commoncrawl.org, because the
index.commoncrawl.org query server is frequently overloaded.
"""
import argparse
import bisect
import csv
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.request

DATA = "https://data.commoncrawl.org/"
COLLINFO = "https://index.commoncrawl.org/collinfo.json"


def http(url, byte_range=None, tries=5):
    headers = {"User-Agent": "bestbuy-price-research/1.0"}
    if byte_range:
        headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
    last = None
    for attempt in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=180).read()
        except Exception as e:
            last = e
            time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"{url}: {last}")


def recent_crawls(n):
    return [c["id"] for c in json.loads(http(COLLINFO))[:n]]


def load_cluster_index(crawl, cache_dir):
    path = os.path.join(cache_dir, f"cluster_{crawl}.idx")
    if not os.path.exists(path):
        print(f"  downloading index for {crawl} (~100 MB, cached afterwards)...", file=sys.stderr)
        with open(path, "wb") as f:
            f.write(http(f"{DATA}cc-index/collections/{crawl}/indexes/cluster.idx"))
    keys, blocks = [], []
    with open(path, encoding="utf8", errors="replace") as f:
        for line in f:
            key, rest = line.rstrip("\n").split("\t", 1)
            keys.append(key)
            blocks.append(rest.split("\t"))
    return keys, blocks


def lookup(crawl, prefix, cache_dir):
    """All index rows in one crawl whose URL key starts with `prefix`."""
    keys, blocks = load_cluster_index(crawl, cache_dir)
    start = max(0, bisect.bisect_left(keys, prefix) - 1)
    rows = []
    for i in range(start, len(keys)):
        if i > start and keys[i][:len(prefix)] > prefix:
            break
        name, offset, length = blocks[i][0], int(blocks[i][1]), int(blocks[i][2])
        raw = http(f"{DATA}cc-index/collections/{crawl}/indexes/{name}", (offset, offset + length - 1))
        for line in gzip.GzipFile(fileobj=io.BytesIO(raw)).read().decode("utf8", "replace").splitlines():
            if line.startswith(prefix):
                urlkey, timestamp, meta = line.split(" ", 2)
                row = json.loads(meta)
                row.update(urlkey=urlkey, timestamp=timestamp, crawl=crawl)
                rows.append(row)
    return rows


def fetch_page(row):
    offset, length = int(row["offset"]), int(row["length"])
    raw = http(DATA + row["filename"], (offset, offset + length - 1))
    parts = gzip.GzipFile(fileobj=io.BytesIO(raw)).read().split(b"\r\n\r\n", 2)
    return parts[2].decode("utf8", "replace") if len(parts) > 2 else ""


def parse_prices(html):
    """Offers from the page's JSON-LD Product block, plus which SKUs are Best Buy (1P) or Marketplace (3P)."""
    sellers = {}
    for sku, cls in re.findall(r'"skuId":"(\d+)","sellerClassification":"(1P|3P)"', html):
        sellers[sku] = cls
    offers = []
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, flags=re.S):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict) or item.get("@type") != "Product":
                continue
            item_offers = item.get("offers") or []
            if isinstance(item_offers, dict):
                item_offers = [item_offers]
            for o in item_offers:
                sku = str(o.get("sku") or item.get("sku"))
                offers.append({"sku": sku, "price": o.get("price"),
                               "condition": o.get("description") or "",
                               "availability": (o.get("availability") or "").split("/")[-1],
                               "seller": sellers.get(sku, "3P?" if len(sku) >= 8 else "1P?"),
                               "model": item.get("model")})
    return offers


def main():
    ap = argparse.ArgumentParser(description="Archived Best Buy page prices from Common Crawl")
    ap.add_argument("url", help="bestbuy.com/product/<slug>/<BSIN> URL")
    ap.add_argument("--crawls", type=int, default=6, help="number of most recent monthly crawls to search")
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/cc-index"))
    ap.add_argument("--csv")
    a = ap.parse_args()

    m = re.search(r"/product/([^/?#]+)/([A-Za-z0-9]{8,12})(?:[/?#]|$)", a.url)
    if not m:
        sys.exit("Expected a URL like https://www.bestbuy.com/product/<slug>/<BSIN>")
    words, bsin = m.group(1).lower().split("-"), m.group(2).lower()
    prefix = "com,bestbuy)/product/" + "-".join(words[:6])  # slugs get reworded over time; match loosely
    os.makedirs(a.cache_dir, exist_ok=True)

    out = []
    for crawl in recent_crawls(a.crawls):
        rows = [r for r in lookup(crawl, prefix, a.cache_dir)
                if r.get("status") == "200" and f"/{bsin}" in r["urlkey"]]
        print(f"{crawl}: {len(rows)} archived page(s)", file=sys.stderr)
        for r in rows:
            for o in parse_prices(fetch_page(r)):
                ts = r["timestamp"]
                out.append({"captured_utc": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}", **o,
                            "page_url": r["url"]})
    out.sort(key=lambda x: x["captured_utc"])
    for o in out:
        print(f"{o['captured_utc']}  SKU {o['sku']:>9} ({o['seller']:3})  ${o['price']:>9}  "
              f"{o['condition']:<20} {o['availability']}")
    if not out:
        print("No archived captures of this product in the selected crawls.")
    if a.csv and out:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)


if __name__ == "__main__":
    main()
