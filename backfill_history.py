#!/usr/bin/env python3
"""Load earlier price history for the tracked laptops from a third-party source.

    python backfill_history.py                          # Honey, every tracked laptop
    python backfill_history.py --sku 6615769 6613868    # Honey, these SKUs only

    # ShopSavvy (paid credits). Without --yes it only prints what the run would cost.
    python backfill_history.py --source shopsavvy --start 2025-11-19 --end 2026-04-23 --limit 5
    python backfill_history.py --source shopsavvy --start 2025-11-19 --end 2026-04-23 --limit 5 --yes

Backfilled rows are stored under their own source label (honey, honey_carried,
shopsavvy, shopsavvy_carried). A first-hand row is never overwritten: every source
keeps its own row per laptop and day, and the views choose between them.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from tracker import collect, db
from tracker.sources import honey, shopsavvy
from tracker.store import LocalStore, now_iso


def _targets(store: LocalStore, args) -> list[str]:
    if args.sku:
        skus = [str(s) for s in args.sku]
    elif args.all:
        skus = sorted(k for k, p in store.products().items() if p.get("active", True))
    else:
        skus = store.tracked_skus()
    if not args.sku:   # featured groups first, so a small --limit pilots the laptops that matter most
        featured = {sku: g["featured_rank"] for g in store.spec_groups() if g.get("featured_rank")
                    for sku in g.get("skus", [])}
        skus.sort(key=lambda s: (featured.get(s, 999), s))
    return skus[:args.limit] if args.limit else skus


def run_honey(store: LocalStore, skus: list[str], args) -> int:
    run_id, started = collect.new_run_id(), now_iso()
    print(f"Honey: reading the price log of {len(skus)} laptops (last {args.days} days)")
    rows, unknown = honey.backfill(skus, days=args.days, run_id=run_id, progress=print)
    inserted = store.insert_snapshots(rows)
    observed = sum(1 for r in rows if r["source"] == honey.SOURCE)
    store.log_run({"id": run_id, "started_at": started, "finished_at": now_iso(), "source": honey.SOURCE,
                   "kind": "backfill", "products_ok": len(skus) - len(unknown), "products_failed": len(unknown),
                   "status": "ok" if not unknown else "partial", "error": None})
    first = min((r["snapshot_date"] for r in rows), default="-")
    print(f"{len(rows)} daily rows ({observed} observed change points, the rest carried forward), "
          f"earliest {first}; {inserted} new rows stored; {len(unknown)} laptops unknown to Honey")
    if unknown:
        print("no Honey record: " + " ".join(unknown[:30]) + (" ..." if len(unknown) > 30 else ""))
    return 0


def run_shopsavvy(store: LocalStore, skus: list[str], args) -> int:
    if not (args.start and args.end):
        print("--source shopsavvy needs --start and --end (YYYY-MM-DD)", file=sys.stderr)
        return 2
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    products = store.products()
    targets = [{"sku": s, "upc": products[s].get("upc")} for s in skus if s in products]
    with_upc = [t for t in targets if t["upc"]]
    estimate = shopsavvy.estimate_credits(len(with_upc), start, end)
    print(f"ShopSavvy: {len(with_upc)} laptops with a UPC, {start} .. {end} "
          f"({len(shopsavvy.windows(start, end))} request window(s)); at most {estimate} credits")
    try:
        client = shopsavvy.ShopSavvy()
        usage = client.usage()
    except shopsavvy.ShopSavvyError as e:
        print(f"ShopSavvy is not usable: {e}", file=sys.stderr)
        return 2
    left = (usage.get("current_period") or {}).get("credits_remaining")
    print(f"credits remaining this period: {left}")
    if not args.yes:
        print("dry run: nothing was requested. Add --yes to spend the credits.")
        return 0
    if left is not None and left < estimate and not args.force:
        print("not enough credits for the worst case; lower --limit, shorten the window, or pass --force",
              file=sys.stderr)
        return 2

    run_id, started = collect.new_run_id(), now_iso()
    status, error = "ok", None
    try:
        rows, skipped = shopsavvy.backfill(targets, start, end, client=client, run_id=run_id, progress=print)
    except shopsavvy.ShopSavvyError as e:
        rows, skipped, status, error = [], {}, "failed", str(e)
        print(f"ShopSavvy request failed: {e}", file=sys.stderr)
    inserted = store.insert_snapshots(rows)
    covered = len({r["sku"] for r in rows})
    store.log_run({"id": run_id, "started_at": started, "finished_at": now_iso(), "source": shopsavvy.SOURCE,
                   "kind": "backfill", "products_ok": covered, "products_failed": len(skipped),
                   "status": status if not skipped else "partial", "error": error})
    print(f"{len(rows)} daily rows for {covered} laptops, {inserted} new rows stored; "
          f"{client.credits_used} credits used")
    for sku, why in sorted(skipped.items()):
        print(f"  skipped {sku}: {why}")
    return 0 if status == "ok" else 3


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["honey", "shopsavvy"], default="honey")
    ap.add_argument("--sku", nargs="+", help="only these SKUs (default: every tracked laptop)")
    ap.add_argument("--all", action="store_true",
                    help="every collected laptop, not only the tracked ones (for the spec explorer)")
    ap.add_argument("--limit", type=int, help="only the first N laptops (featured groups first)")
    ap.add_argument("--days", type=int, default=400, help="Honey: how far back to read (default 400)")
    ap.add_argument("--start", help="ShopSavvy: first day, YYYY-MM-DD")
    ap.add_argument("--end", help="ShopSavvy: last day, YYYY-MM-DD")
    ap.add_argument("--yes", action="store_true", help="ShopSavvy: really spend the credits")
    ap.add_argument("--force", action="store_true", help="ShopSavvy: run even if the estimate exceeds the balance")
    args = ap.parse_args()

    store = LocalStore()
    skus = _targets(store, args)
    if not skus:
        print("nothing is tracked yet: run collect_top_laptops.py and build_groups.py first", file=sys.stderr)
        return 1
    code = run_honey(store, skus, args) if args.source == "honey" else run_shopsavvy(store, skus, args)
    if code == 0 and (args.source == "honey" or args.yes):
        db.sync_safely(store, ("snapshots", "runs"))
    return code


if __name__ == "__main__":
    sys.exit(main())
