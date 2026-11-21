"""Backup source B (optional, paid): the ShopSavvy Data API.

The only source here with a published, documented API for retailer price history,
and the route to history older than Honey's log. Written from ShopSavvy's OpenAPI
spec (https://api.shopsavvy.com/v1/openapi.json, v1.3.1) and documentation; it has
NOT been run against the live API yet because no key was available. Pilot it on a
few laptops first (see backfill_history.py --source shopsavvy).

    GET /v1/products/offers/history?ids=<upc,...>&start=YYYY-MM-DD&end=YYYY-MM-DD&retailer=bestbuy.com
    Authorization: Bearer $SHOPSAVVY_API_KEY

What the documentation fixes:

* `ids` takes up to 100 comma-separated identifiers of any type; UPCs are matched
  exactly as barcodes, so the tracker sends each laptop's UPC. Results are mapped
  back through `match.identifier`, never by position (misses are simply absent).
* `retailer` is a bare domain (`bestbuy.com`); the response names it "Best Buy".
* A window may span at most 366 days: longer ranges are split into several requests.
* The response is data[] (products) -> offers[] (one per listing) -> history[]
  (points, newest first: timestamp, price, currency, optional availability).
* Cost: 2 credits per product with offers + 1 credit per day of the window, the
  per-day part charged once per request. So always batch, and never search by
  keyword. A request that finds no offers costs nothing; a 404 means the same.
* Only first-party New offers are stored: `condition == "new"`, `is_marketplace`
  not true, no `seller`. Marketplace offers on bestbuy.com are a different price.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import config

SOURCE = "shopsavvy"
SOURCE_CARRIED = "shopsavvy_carried"

BASE_URL = "https://api.shopsavvy.com/v1"
RETAILER = "bestbuy.com"
MAX_IDS = 100
MAX_WINDOW_DAYS = 365          # (end - start); satisfies both wordings of the 366-day limit
CREDITS_PER_PRODUCT = 2        # with `retailer` set; 3 without


class ShopSavvyError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, action: str | None = None):
        super().__init__(f"{code} (HTTP {status}): {message}" + (f" | {action}" if action else ""))
        self.status, self.code, self.action = status, code, action


class ShopSavvy:
    def __init__(self, api_key: str | None = None, *, base_url: str = BASE_URL, timeout: float = 120.0):
        key = (api_key or config.SHOPSAVVY_API_KEY or "").strip().strip("'\"")
        if not key:
            raise ShopSavvyError(0, "NO_API_KEY", "SHOPSAVVY_API_KEY is not set",
                                 "put the key in .env; the tracker runs without it")
        self.key, self.base_url, self.timeout = key, base_url.rstrip("/"), timeout
        self.credits_used = 0
        self.credits_remaining: int | None = None

    def _get(self, path: str, params: dict, *, empty_on_404: bool = False) -> dict:
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.key}",
                                                   "Accept": "application/json",
                                                   "User-Agent": "pc-pricing-tracker/1.0"})
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = json.loads(r.read().decode())
                    break
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = {}
                detail = body.get("error_detail") or {}
                code = detail.get("code") or f"HTTP_{e.code}"
                if e.code == 404 and empty_on_404:        # none of the ids matched: nothing charged
                    return {"success": True, "data": [], "meta": body.get("meta") or {}}
                if code == "ERR_RATE_LIMIT" and attempt < 3:
                    time.sleep(min(int(e.headers.get("Retry-After") or 30), 120))
                    continue
                if e.code >= 500 and attempt < 3:
                    time.sleep(5 * attempt)
                    continue
                raise ShopSavvyError(e.code, code, detail.get("message") or body.get("error") or raw[:200],
                                     detail.get("action")) from None
        meta = body.get("meta") or {}
        self.credits_used += int(meta.get("credits_used") or 0)
        if meta.get("credits_remaining") is not None:
            self.credits_remaining = int(meta["credits_remaining"])
        return body

    def usage(self) -> dict:
        """Credits left this billing period. Costs no credits."""
        return (self._get("/usage", {}).get("data") or {})

    def history(self, ids: list[str], start: dt.date, end: dt.date) -> list[dict]:
        """Best Buy price history for up to 100 identifiers in one request."""
        if not 1 <= len(ids) <= MAX_IDS:
            raise ValueError(f"history takes 1..{MAX_IDS} identifiers per request, got {len(ids)}")
        if (end - start).days > MAX_WINDOW_DAYS or end < start:
            raise ValueError("history window must be 0..365 days")
        body = self._get("/products/offers/history",
                         {"ids": ",".join(ids), "start": start.isoformat(), "end": end.isoformat(),
                          "retailer": RETAILER}, empty_on_404=True)
        self._keep_raw(body, f"history_{start}_{end}_{len(ids)}ids")
        return body.get("data") or []

    @staticmethod
    def _keep_raw(body: dict, name: str) -> None:
        """Every answer was paid for in credits: keep it, so a parsing mistake never costs a second request."""
        try:
            folder = config.DATA_DIR / "shopsavvy_raw"
            folder.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            (folder / f"{name}_{stamp}.json").write_text(json.dumps(body), encoding="utf-8")
        except OSError:
            pass


# --------------------------------------------------------------------------- planning
def windows(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    """Split [start, end] into consecutive windows the API accepts."""
    out = []
    while start <= end:
        stop = min(start + dt.timedelta(days=MAX_WINDOW_DAYS), end)
        out.append((start, stop))
        start = stop + dt.timedelta(days=1)
    return out


def estimate_credits(n_products: int, start: dt.date, end: dt.date) -> int:
    """Upper bound: every product has Best Buy offers, days counted inclusively."""
    total = 0
    for w_start, w_end in windows(start, end):
        requests = -(-n_products // MAX_IDS)
        total += CREDITS_PER_PRODUCT * n_products + requests * ((w_end - w_start).days + 1)
    return total


# --------------------------------------------------------------------------- parsing
def _sku_in_url(url: str | None) -> str | None:
    m = re.search(r"/sku/(\d{6,9})|[?&]skuId=(\d{6,9})|/(\d{6,9})\.p(?:[?/#]|$)", url or "")
    return next((g for g in m.groups() if g), None) if m else None


def _is_best_buy_first_party(offer: dict) -> bool:
    retailer = (offer.get("retailer") or "").replace(" ", "").lower()
    host = urllib.parse.urlparse(offer.get("URL") or "").netloc.lower()
    return ((retailer in ("bestbuy", "bestbuy.com") or host.endswith("bestbuy.com"))
            and (offer.get("condition") or "new") == "new"
            and not offer.get("is_marketplace")
            and not offer.get("seller"))


def pick_offer(entry: dict, sku: str) -> tuple[dict | None, str]:
    """The Best Buy-sold New offer for this SKU in one product entry, with the reason
    when there is none. An offer whose URL names a different SKU is another variant."""
    known = str((entry.get("identifiers") or {}).get("bestbuy") or "")
    candidates = [o for o in entry.get("offers") or [] if _is_best_buy_first_party(o)]
    if not candidates:
        return None, "no first-party New Best Buy offer"
    exact = [o for o in candidates if _sku_in_url(o.get("URL")) == str(sku)]
    if exact:
        return max(exact, key=lambda o: len(o.get("history") or [])), "sku in offer URL"
    unlabelled = [o for o in candidates if _sku_in_url(o.get("URL")) is None]
    if unlabelled and known == str(sku):
        return max(unlabelled, key=lambda o: len(o.get("history") or [])), "sku in identifiers.bestbuy"
    return None, f"Best Buy offer is for another SKU ({known or 'unknown'})"


def offer_rows(sku: str, offer: dict, *, run_id: int | None = None) -> list[dict]:
    """One row per day from an offer's history: the last observation of each UTC day
    is a `shopsavvy` row, the days between observations are `shopsavvy_carried`.
    Nothing is carried past the last observation."""
    per_day: dict[dt.date, tuple[str, float, bool | None]] = {}
    for point in offer.get("history") or []:
        ts, price = point.get("timestamp"), point.get("price")
        if not ts or not isinstance(price, (int, float)) or price <= 0:
            continue
        if point.get("currency") != "USD":       # null currency: never assume dollars
            continue
        day = dt.date.fromisoformat(ts[:10])
        stock = {"in": True, "out": False}.get(point.get("availability"))
        if day not in per_day or ts > per_day[day][0]:
            per_day[day] = (ts, float(price), stock)
    if not per_day:
        return []
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows, days = [], sorted(per_day)
    day, price = days[0], None
    while day <= days[-1]:
        observed = day in per_day
        if observed:
            _, price, stock = per_day[day]
        rows.append({"sku": str(sku), "snapshot_date": day.isoformat(), "price": price, "list_price": None,
                     "in_stock": stock if observed else None,
                     "source": SOURCE if observed else SOURCE_CARRIED, "captured_at": stamp, "run_id": run_id})
        day += dt.timedelta(days=1)
    return rows


def backfill(products: list[dict], start: dt.date, end: dt.date, *, client: ShopSavvy,
             run_id: int | None = None, progress=None) -> tuple[list[dict], dict[str, str]]:
    """History rows for products (dicts with sku and upc). Returns (rows, {sku: why skipped})."""
    skipped = {str(p["sku"]): "no UPC on file" for p in products if not p.get("upc")}
    by_upc = {str(p["upc"]).strip(): str(p["sku"]) for p in products if p.get("upc")}
    rows: list[dict] = []
    seen: set[str] = set()
    upcs = list(by_upc)
    for w_start, w_end in windows(start, end):
        for i in range(0, len(upcs), MAX_IDS):
            chunk = upcs[i:i + MAX_IDS]
            entries = client.history(chunk, w_start, w_end)
            for entry in entries:
                upc = str((entry.get("match") or {}).get("identifier") or entry.get("barcode") or "").strip()
                sku = by_upc.get(upc)
                if not sku:
                    continue
                offer, why = pick_offer(entry, sku)
                if not offer:
                    skipped.setdefault(sku, why)
                    continue
                new = offer_rows(sku, offer, run_id=run_id)
                if new:
                    seen.add(sku)
                    skipped.pop(sku, None)
                    rows.extend(new)
                else:
                    skipped.setdefault(sku, "offer has no usable history points")
            if progress:
                progress(f"  [shopsavvy] {w_start}..{w_end}: {len(chunk)} ids -> {len(entries)} products, "
                         f"{client.credits_used} credits used so far"
                         + (f", {client.credits_remaining} left" if client.credits_remaining is not None else ""))
    for sku in by_upc.values():
        if sku not in seen:
            skipped.setdefault(sku, "not found by UPC")
    return rows, skipped
