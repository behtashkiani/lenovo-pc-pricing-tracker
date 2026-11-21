#!/usr/bin/env python3
"""Add single laptops to the collection by Best Buy URL or SKU, and pin them.

    python add_to_collection.py "https://www.bestbuy.com/site/6615769.p?skuId=6615769"
    python add_to_collection.py 6615769 6613868
    python add_to_collection.py "https://www.bestbuy.com/product/<slug>/<BSIN>/sku/6613868"

A pinned laptop goes through the same normalise-and-group step as a crawled one,
is always priced daily, and its exact group is always among the featured groups.
New-style links without a SKU (/product/<slug>/<BSIN>) are resolved against the
collection; if the laptop was never crawled, pass the SKU shown on the product page.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from tracker import collect, config, db
from tracker.sources import bestbuy_graphql as bb
from tracker.store import LocalStore, now_iso


def resolve(arg: str, store: LocalStore) -> str | None:
    """SKU for a command-line argument: a bare SKU, a URL carrying one, or a BSIN we already know."""
    arg = arg.strip()
    if arg.isdigit() and 6 <= len(arg) <= 9:
        return arg
    sku = bb.sku_from_url(arg)
    if sku:
        return sku
    bsin = bb.bsin_from_url(arg)
    if bsin:
        known = [p for p in store.products().values() if (p.get("bsin") or "").upper() == bsin]
        known.sort(key=lambda p: (p.get("seller_class") != "1P", (p.get("condition") or "new") != "new", p["sku"]))
        if known:
            return known[0]["sku"]
    return None


async def run(args) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    store = LocalStore()
    skus, bad = [], []
    for arg in args.items:
        sku = resolve(arg, store)
        (skus if sku else bad).append(sku or arg)
    for arg in bad:
        print(f"no SKU found in {arg!r}: add '/sku/<number>' to the link or pass the SKU "
              "(it is shown on the product page)", file=sys.stderr)
    if not skus:
        return 2

    run_id, started = collect.new_run_id(), now_iso()
    try:
        result = await bb.run_with_session(
            lambda client: collect.collect_skus(client, store, skus, pinned=not args.no_pin),
            **bb.session_kwargs(args))
    except bb.Blocked as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        return 3
    products = result["products"]
    store.insert_snapshots(collect.snapshot_rows(products, run_id))
    store.log_run({"id": run_id, "started_at": started, "finished_at": now_iso(), "source": bb.SOURCE,
                   "kind": "add", "products_ok": len(products), "products_failed": len(result["missing"]),
                   "status": "ok" if not result["missing"] else "partial", "error": None})

    specs, groups = collect.rebuild_groups(store, config.ANCHOR_BRAND)
    by_sku = {s.sku: s for s in specs}
    by_key = {g["group_key"]: g for g in groups}
    for p in products:
        s = by_sku[p.sku]
        print(f"{p.sku}  {p.title}")
        if p.seller_class != "1P" or p.condition != "new":
            print(f"    note: {p.seller_class} offer, condition {p.condition}: it is priced daily because it is "
                  "pinned, but only New offers sold by Best Buy count towards a group")
        if s.needs_review:
            print("    needs review, not grouped: " + "; ".join(s.review_notes))
        elif s.group_key in by_key:
            g = by_key[s.group_key]
            print(f"    group: {g['label']}  ({', '.join(g['brands'])})"
                  + (f"  featured #{g['featured_rank']}" if g.get("featured_rank") else ""))
    for sku in result["missing"]:
        print(f"{sku}: Best Buy returned no such product", file=sys.stderr)
    db.sync_safely(store, ("brands", "products", "specs", "groups", "snapshots", "runs"))
    return 0 if not result["missing"] and not bad else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("items", nargs="+", metavar="URL_OR_SKU")
    ap.add_argument("--no-pin", action="store_true", help="add without pinning")
    bb.add_session_args(ap)
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
