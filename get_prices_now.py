#!/usr/bin/env python3
"""Fetch today's Best Buy price for every tracked laptop and log the run.

    python get_prices_now.py            # the command the daily scheduler runs
    python get_prices_now.py --sku 6615769 6613868

One price row per SKU per day (UTC), first-hand from Best Buy's gateway. A SKU the
gateway could not price falls back to Honey's log; a run never fails because of a
single SKU, and running it twice on one day adds nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from tracker import collect, db
from tracker.sources import bestbuy_graphql as bb
from tracker.sources import honey
from tracker.store import LocalStore, now_iso


async def run(args) -> int:
    store = LocalStore()
    if args.sku:
        skus = [str(s) for s in args.sku]
    elif args.all:      # every collected laptop: keeps the spec explorer's first-hand prices growing too
        skus = sorted(k for k, p in store.products().items() if p.get("active", True))
    else:
        skus = store.tracked_skus()
    if not skus:
        print("nothing is tracked yet: run collect_top_laptops.py and build_groups.py first", file=sys.stderr)
        return 1

    run_id, started = collect.new_run_id(), now_iso()
    priced: dict[str, bb.Product] = {}
    not_returned: set[str] = set()
    calls = [0]

    async def job(client: bb.BestBuyGQL) -> None:
        for attempt in range(1, 4):                      # retry with backoff, max 3
            todo = [s for s in skus if s not in priced]
            if not todo:
                break
            found, missing = await client.prices(todo)
            for p in found:
                if p.price is not None:
                    priced[p.sku] = p
            not_returned.clear()
            not_returned.update(missing)
            calls[0] = client.calls
            if len(priced) < len(skus):
                await asyncio.sleep(2.0 * attempt)

    error = None
    try:
        await bb.run_with_session(job, **bb.session_kwargs(args))
    except bb.Blocked as e:
        error = f"blocked: {e}"
        bb.log(f"\nBLOCKED: {e}\nBest Buy's edge refused this client; stopping (never escalating).")
    except Exception as e:  # noqa: BLE001 - a gateway error must still leave a run log and a fallback
        error = f"{type(e).__name__}: {e}"
        bb.log(f"\nERROR: {error}")

    rows = collect.snapshot_rows(list(priced.values()), run_id)
    flagged = collect.flag_outliers(rows, store.snapshots())
    inserted = store.insert_snapshots(rows)
    failed = [s for s in skus if s not in priced]

    # Delisted products: only an explicit "no such SKU" answer counts, never a blocked run.
    dropped = store.record_misses(list(priced), sorted(not_returned)) if error is None else []

    recovered = 0
    if failed and not args.no_fallback:
        try:
            fallback_rows, _ = honey.backfill(failed, days=7, run_id=run_id, progress=bb.log)
            recovered = store.insert_snapshots(fallback_rows)
        except Exception as e:  # noqa: BLE001 - the fallback is best effort
            bb.log(f"  [honey] fallback failed: {e}")

    status = "ok" if not failed and error is None else ("partial" if priced else "failed")
    store.log_run({"id": run_id, "started_at": started, "finished_at": now_iso(), "source": bb.SOURCE,
                   "kind": "prices", "products_ok": len(priced), "products_failed": len(failed),
                   "status": status, "error": error})

    print(f"{len(priced)}/{len(skus)} laptops priced in {calls[0]} requests; {inserted} new price rows"
          + (f"; {len(failed)} failed" if failed else "")
          + (f"; {recovered} rows recovered from Honey" if recovered else "")
          + (f"; {flagged} flagged by the sanity check" if flagged else "")
          + (f"; {len(dropped)} marked inactive (no longer listed)" if dropped else ""))
    if failed:
        print("failed: " + " ".join(failed[:20]) + (" ..." if len(failed) > 20 else ""))
    db.sync_safely(store, ("products", "snapshots", "runs"))
    return 0 if status == "ok" else 3


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sku", nargs="+", help="price these SKUs instead of the tracked set")
    ap.add_argument("--all", action="store_true",
                    help="price every collected laptop (about 26 requests), not only the tracked groups")
    ap.add_argument("--no-fallback", action="store_true", help="do not read Honey for SKUs that failed")
    bb.add_session_args(ap)
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
