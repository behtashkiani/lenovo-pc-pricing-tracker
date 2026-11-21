#!/usr/bin/env python3
"""Crawl Best Buy's laptop category brand by brand and save every product with its
raw specification table.

    python collect_top_laptops.py --brand lenovo          # one brand
    python collect_top_laptops.py --all-brands            # every enabled brand in the brands table
    python collect_top_laptops.py --brand hp --limit 40   # the 40 best-selling only

By default only New laptops sold by Best Buy itself are collected. Run
build_groups.py afterwards to normalise and group what was collected.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from tracker import collect, config, db
from tracker.sources import bestbuy_graphql as bb
from tracker.store import LocalStore, now_iso


async def run(args) -> int:
    store = LocalStore()
    if args.all_brands:
        brands = [b["name"] for b in store.brands() if b.get("enabled", True)]
        # anchor brand first: its laptops define the baseline
        brands.sort(key=lambda b: b.lower() != config.ANCHOR_BRAND.lower())
    else:
        brands = [collect.resolve_brand(store, args.brand)]
        store.add_brand(brands[0])

    run_id = collect.new_run_id()
    started = now_iso()
    stats: list[dict] = []
    calls = 0

    async def job(client: bb.BestBuyGQL):
        nonlocal calls
        done = {s["brand"] for s in stats}  # a rejected cached session re-enters here: resume, don't restart
        for brand in brands:
            if brand in done:
                continue
            before = client.calls
            s = await collect.collect_brand(
                client, store, brand, limit=args.limit, marketplace=args.include_marketplace,
                condition=None if args.condition == "any" else args.condition,
                refresh_specs=args.refresh_specs)
            inserted = store.insert_snapshots(collect.snapshot_rows(s.pop("products"), run_id))
            s["prices_saved"] = inserted
            stats.append(s)
            calls += client.calls - before
            bb.log(f"{s['brand']}: {s['listed']} listed, {s['new']} new, {s['updated']} updated, "
                   f"{s['specs_fetched']} spec tables fetched, {inserted} prices saved"
                   + (f", {len(s['specs_missing'])} without details" if s["specs_missing"] else ""))

    status, error = "ok", None
    try:
        await bb.run_with_session(job, **bb.session_kwargs(args))
    except bb.Blocked as e:
        status, error = ("partial" if stats else "failed"), f"blocked: {e}"
        bb.log(f"\nBLOCKED: {e}\nBest Buy's edge refused this client; stopping (try again later).")
    store.log_run({"id": run_id, "started_at": started, "finished_at": now_iso(), "source": bb.SOURCE,
                   "kind": "collect", "products_ok": sum(s["listed"] for s in stats),
                   "products_failed": sum(len(s["specs_missing"]) for s in stats),
                   "status": status, "error": error})

    total = len(store.products())
    print(f"collected {sum(s['listed'] for s in stats)} laptops across {len(stats)} brand(s) "
          f"in {calls} requests; the collection now holds {total} products")
    print("next: python build_groups.py")
    db.sync_safely(store, ("brands", "products", "snapshots", "runs"))
    return 0 if status == "ok" else 3


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--brand", help="brand name as in the brands table, e.g. lenovo")
    g.add_argument("--all-brands", action="store_true", help="every enabled brand in the brands table")
    ap.add_argument("--limit", type=int, help="only the first N laptops in Best Buy's best-selling order")
    ap.add_argument("--condition", choices=["new", "open-box", "refurbished", "any"], default="new")
    ap.add_argument("--include-marketplace", action="store_true",
                    help="also collect offers from third-party Marketplace sellers (never tracked by default)")
    ap.add_argument("--refresh-specs", action="store_true",
                    help="re-fetch the specification table of products already in the collection")
    bb.add_session_args(ap)
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
