#!/usr/bin/env python3
"""Normalise the collected laptops, group equivalent ones and mark which groups are tracked.

    python build_groups.py                 # rebuild and print the summary
    python build_groups.py --show featured # list the featured groups with their members
    python build_groups.py --show review   # products the normaliser could not place

Run it after any collect or add. It reads the raw spec tables saved by the
collector, so a change to the normalisation rules is applied to every product
without scraping again.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from tracker import collect, config, db, grouping
from tracker.normalise import LEVELS, NORMALISER_VERSION
from tracker.store import LocalStore


def _latest_prices(store: LocalStore) -> dict[str, float]:
    latest: dict[str, tuple[str, float]] = {}
    for r in store.snapshots():
        if r["source"] == "bestbuy_graphql" and r["snapshot_date"] >= latest.get(r["sku"], ("", 0))[0]:
            latest[r["sku"]] = (r["snapshot_date"], r["price"])
    return {sku: price for sku, (_, price) in latest.items()}


def _print_group(g: dict, specs: dict, products: dict, prices: dict) -> None:
    rank = f"#{g['featured_rank']} " if g.get("featured_rank") else ""
    print(f"\n{rank}[{g['level']}] {g['label']}")
    anchor = config.ANCHOR_BRAND.lower()
    others = [b for b in g["brands"] if b.lower() != anchor]
    print(f"    {g['competitor_count']} competitor brand(s): {', '.join(others) or 'none'}   ({g['member_count']} laptops)")
    for sku in sorted(g["skus"], key=lambda k: ((specs[k].brand or "").lower() != anchor, specs[k].brand or "", k)):
        s = specs[sku]
        price = prices.get(sku)
        shown = f"${price:>8.2f}" if price is not None else "        ?"
        size = f'{s.screen_size_in:g}"' if s.screen_size_in else "?"
        print(f"      {s.brand or '?':<10}{sku:>9}  {shown}  {size:<6}{s.cpu_name or '?':<32}"
              f"{(products[sku].get('title') or '')[:70]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anchor", default=config.ANCHOR_BRAND, help="brand that defines the baseline (default: %(default)s)")
    ap.add_argument("--show", choices=["featured", "tracked", "unmatched", "review"],
                    help="print one section in full")
    ap.add_argument("--level", choices=LEVELS, default="exact", help="level for --show tracked/unmatched")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):  # group labels contain a middle dot
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    store = LocalStore()
    products = store.products()
    if not products:
        print("the collection is empty: run collect_top_laptops.py first", file=sys.stderr)
        return 1

    specs, groups = collect.rebuild_groups(store, args.anchor)

    by_sku = {s.sku: s for s in specs}
    tracked = grouping.tracked_skus(products, specs, groups)
    review = [s for s in specs if s.needs_review]
    eligible = sum(1 for p in products.values() if grouping.eligible(p))

    print(f"normaliser {NORMALISER_VERSION}: {len(products)} products ({eligible} New, sold by Best Buy), "
          f"{len(review)} need review, anchor brand {args.anchor}")
    print(f"{'level':<9}{'groups':>7}{'with ' + args.anchor:>14}{'+1 brand':>10}{'+2':>5}{'+3':>5}{'+4':>5}{'+5':>5}")
    for level in LEVELS:
        lg = [g for g in groups if g["level"] == level]
        anchored = [g for g in lg if g["has_anchor"]]
        counts = [sum(1 for g in anchored if g["competitor_count"] >= n) for n in range(1, 6)]
        print(f"{level:<9}{len(lg):>7}{len(anchored):>14}{counts[0]:>10}{counts[1]:>5}{counts[2]:>5}{counts[3]:>5}{counts[4]:>5}")
    featured = sorted((g for g in groups if g.get("featured_rank")), key=lambda g: g["featured_rank"])
    print(f"tracked: {sum(1 for g in groups if g['tracked'])} groups, {len(tracked)} laptops get a daily price; "
          f"{len(featured)} featured groups")

    prices = _latest_prices(store)
    if args.show == "featured" or args.show is None:
        for g in featured:
            if args.show:
                _print_group(g, by_sku, products, prices)
            else:
                print(f"  #{g['featured_rank']:<2} [{g['level']:<7}] {g['competitor_count']} competitors  {g['label']}"
                      f"  ({', '.join(b for b in g['brands'] if b.lower() != args.anchor.lower())})")
    if args.show == "tracked":
        for g in (g for g in groups if g["level"] == args.level and g["tracked"]):
            _print_group(g, by_sku, products, prices)
    if args.show == "unmatched":
        for g in grouping.unmatched_anchor_groups(groups, args.level):
            _print_group(g, by_sku, products, prices)
    if args.show == "review":
        for s in review:
            print(f"  {s.sku}  {s.brand or '?':<10}{(products[s.sku].get('title') or '')[:80]}")
            for note in s.review_notes:
                print(f"      {note}")
    notes = Counter(n.split(":")[0] for s in specs for n in s.review_notes if not s.needs_review)
    if notes and args.show is None:
        print("cross-check notes on grouped products: " + ", ".join(f"{k} {v}" for k, v in sorted(notes.items())))

    db.sync_safely(store, ("brands", "products", "specs", "groups"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
