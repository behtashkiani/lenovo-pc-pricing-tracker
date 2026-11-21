"""
Standalone Best Buy price scraper: plain async HTTP calls to the site's GraphQL
gateway (https://www.bestbuy.com/gateway/graphql).

Session:
  * The gateway only answers a session whose Akamai cookies (bm_s in particular)
    were earned by a real browser running the site's scripts. A plain HTTP GET of
    the homepage is never enough, so there is no plain-HTTP warm-up.
  * Warm-up therefore opens Chrome once (Playwright), waits until the gateway
    answers from inside the page, and hands the cookies to curl_cffi.
  * The accepted cookies are cached in .bestbuy_session.json and reused on later
    runs; the browser opens again only when the gateway rejects the cached session
    (or with --fresh). --cookie-file uses a pasted Cookie header instead.
  * All scraping itself is curl_cffi with Chrome TLS/HTTP2 impersonation.

  * PriceBatch : aliased productBySkuId -> up to BATCH_SIZE SKUs priced per request
  * PlpSearch  : category/brand listing, 15 products + prices per page, max 100 pages
                 (1500 results); bigger result sets are split recursively by facet.

Usage:
  python bestbuy_gql.py --sku 12789225 6668413
  python bestbuy_gql.py --url https://www.bestbuy.com/product/.../JJGH3Q8P2K/sku/12789225
  python bestbuy_gql.py --brand Lenovo
  python bestbuy_gql.py --all
  python bestbuy_gql.py --refresh out/latest.csv

  python bestbuy_gql.py --sku 12789225 --fresh        # ignore the cached session

Requires: pip install "curl_cffi>=0.7" playwright   (plus an installed Chrome,
          or `playwright install chromium`)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests as cffi_requests

# --------------------------------------------------------------------------- config
ENDPOINT = "https://www.bestbuy.com/gateway/graphql"
HOME_URL = "https://www.bestbuy.com/"
LAPTOPS_CATEGORY = "pcmcat138500050001"
CONCURRENCY = 4
BATCH_SIZE = 20
JITTER = (0.15, 0.6)
MAX_RETRIES = 5
TIMEOUT = 25.0
MAX_PAGES = 100
SLICE_LIMIT = 1450
SPLIT_FACETS = ["brand_facet", "currentprice_facet", "systemmemoryrange_facet",
                "laptopscreensizerange_facet", "totalstoragecapacityrange_facet", "processorbrand_facet"]
OUT_DIR = Path("out")
# Cookies of the last session the gateway accepted (treat like a login cookie file).
SESSION_FILE = Path(__file__).with_name(".bestbuy_session.json")

PRICE_INPUT = {"salesChannel": "LargeView", "context": "plp"}

# curl_cffi sets User-Agent / sec-ch-ua / HTTP/2 preamble itself; these are the
# GraphQL-specific extras Best Buy's gateway expects.
HEADERS = {
    "x-dynatrace": "",
    "x-page-request-id": "9eba583e-8c55-48be-89d1-aca372f3ba9b",
    "accept": "application/graphql-response+json,application/json;q=0.9",
    "content-type": "application/json",
    "x-client-id": "pdp-web",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

# Pin the impersonated browser if you want reproducible fingerprints; "chrome"
# resolves to the newest fingerprint supported by the installed curl_cffi.
IMPERSONATE = "chrome"

PRODUCT_FIELDS = "skuId brand name{short} price(input:$in){ customerPrice regularPrice }"

SEARCH_QUERY = """
query PlpSearch($input:SearchInput!,$pagination:SearchPagination!,$filter:SearchFilter,
                $sort:SearchSort,$testing:SearchTesting!,$nd:Int,$in:ProductItemPriceInput!){
  search(input:$input,pagination:$pagination,filter:$filter,sort:$sort,testing:$testing,numDetailedProducts:$nd){
    numFound
    facets{ facetField values{ value count } }
    documents{
      __typename
      ...on SearchProduct{ product{ ...P } }
      ...on SearchDetailedProduct{ product{ ...P } }
    }
  }
}
fragment P on Product{ %s }
""" % PRODUCT_FIELDS


class Blocked(RuntimeError):
    """The edge (Akamai) refused the request rather than GraphQL answering it."""


# --------------------------------------------------------------------------- model
@dataclass
class Item:
    sku: str
    name: str | None
    brand: str | None
    price: float | None
    regular_price: float | None
    savings: float | None
    url: str
    scraped_at: str

    @classmethod
    def from_product(cls, p: dict | None) -> "Item | None":
        if not p or not p.get("skuId"):
            return None
        pr = p.get("price") or {}
        cur, reg = pr.get("customerPrice"), pr.get("regularPrice")
        return cls(
            sku=p["skuId"],
            name=(p.get("name") or {}).get("short"),
            brand=p.get("brand"),
            price=cur,
            regular_price=reg,
            savings=round(reg - cur, 2) if cur is not None and reg is not None else None,
            url=f"https://www.bestbuy.com/site/{p['skuId']}.p?skuId={p['skuId']}",
            scraped_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )


# --------------------------------------------------------------------------- client
def abck_state(abck: str | None) -> str:
    """'0' = validated by Akamai's JS sensor, '-1' = issued but never validated."""
    if not abck:
        return "missing"
    m = re.match(r"[0-9A-Fa-f]+~(-?\d+)~", abck)
    return m.group(1) if m else "unparsed"


def parse_cookie_header(header: str) -> list[dict]:
    """'a=1; b=2' (as copied from DevTools / bestbuy.sh) -> cookie dicts."""
    out = []
    for part in header.strip().strip("'\"").split("; "):
        if "=" in part:
            name, value = part.split("=", 1)
            out.append({"name": name.strip(), "value": value, "domain": ".bestbuy.com", "path": "/"})
    return out


# Runs inside the warm-up page: any HTTP answer from the gateway (even a GraphQL
# error) means the edge accepted the session; a flagged session has its stream
# reset, which fetch() surfaces as a TypeError.
GATEWAY_PROBE_JS = """async () => {
  try {
    const r = await fetch('/gateway/graphql', {method: 'POST', credentials: 'include',
      headers: {'content-type': 'application/json', 'x-client-id': 'pdp-web',
                'accept': 'application/graphql-response+json,application/json;q=0.9',
                'x-requested-for-operation-name': 'Probe'},
      body: JSON.stringify({operationName: 'Probe', query: 'query Probe{__typename}', variables: {}})});
    return r.status === 403 ? 'http 403' : 'ok';
  } catch (e) { return 'stream reset'; }
}"""


def load_session() -> list[dict] | None:
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))["cookies"] or None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_session(cookies: list[dict]) -> None:
    try:
        SESSION_FILE.write_text(json.dumps(
            {"saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cookies": cookies},
            indent=1), encoding="utf-8")
    except OSError as e:
        print(f"  [session] could not write {SESSION_FILE.name}: {e}", file=sys.stderr)


def browser_cookies(headless: bool, wait_s: float = 10.0, attempts: int = 5) -> tuple[list[dict], bool]:
    """Load bestbuy.com in real Chrome via Playwright so Akamai's scripts run.
    Returns (cookies, accepted): accepted is True when the gateway answered a
    probe request made from inside that browser session."""
    from playwright.sync_api import sync_playwright

    # Playwright's default launch sets navigator.webdriver=true; Akamai's sensor
    # reports it and _abck then stays ~-1~ forever. Dropping the automation
    # switches (plus a little pointer activity below) gets ~0~ in a few seconds.
    launch = {"headless": headless,
              "args": ["--disable-blink-features=AutomationControlled"],
              "ignore_default_args": ["--enable-automation"]}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", **launch)
        except Exception:  # installed Chrome not found -> bundled Chromium
            browser = p.chromium.launch(**launch)
        # _abck ~0~ is neither sufficient nor strictly necessary: in testing about
        # half of the ~0~ sessions still had their gateway streams reset (for the
        # browser itself too, not just curl_cffi), and one ~-1~ session was
        # accepted. So give the sensor a few seconds, then probe the gateway from
        # inside the page and only hand over cookies from a session the edge
        # actually answers; otherwise start a fresh context and try again.
        state, cookies, accepted = "missing", [], False
        for attempt in range(1, attempts + 1):
            ctx = browser.new_context(locale="en-US")
            ctx.add_cookies([{"name": "intl_splash", "value": "false", "domain": ".bestbuy.com", "path": "/"}])
            page = ctx.new_page()
            try:
                page.goto(HOME_URL + "?intl=nosplash", wait_until="domcontentloaded", timeout=60_000)
                deadline = time.monotonic() + wait_s
                while time.monotonic() < deadline:
                    cookies = [c for c in ctx.cookies() if "bestbuy.com" in c["domain"]]
                    state = abck_state(next((c["value"] for c in cookies if c["name"] == "_abck"), None))
                    if state == "0":
                        break
                    page.mouse.move(random.randint(100, 900), random.randint(100, 600), steps=12)
                    page.mouse.wheel(0, random.randint(100, 400))
                    page.wait_for_timeout(1000)
                probe = page.evaluate(GATEWAY_PROBE_JS)
                cookies = [c for c in ctx.cookies() if "bestbuy.com" in c["domain"]]
            except Exception as e:
                probe = f"error {e!r}"[:80]
            accepted = probe == "ok"
            print(f"  [warmup:browser] attempt {attempt}/{attempts}: _abck state={state}, "
                  f"{len(cookies)} cookies, gateway probe={probe}", file=sys.stderr)
            ctx.close()
            if accepted:
                break
        browser.close()

    if not accepted:
        print("  [warmup:browser] no session was accepted by the gateway"
              f"{' (headless sessions were never accepted in testing; drop --headless)' if headless else ''}",
              file=sys.stderr)
    return [{k: c[k] for k in ("name", "value", "domain", "path")} for c in cookies], accepted


class BestBuyGQL:
    def __init__(self, concurrency: int, seed_cookies: list[dict]):
        self.sem = asyncio.Semaphore(concurrency)
        self.calls = 0
        # Cookies of a browser-earned session (warm-up, cache, or a pasted
        # cookie header); every HTTP session starts from them.
        self.seed_cookies = seed_cookies
        # curl_cffi Session objects are not documented as safe for concurrent
        # use from multiple threads, so each worker thread gets its own session.
        self._local = threading.local()

    def _session(self) -> cffi_requests.Session:
        s: cffi_requests.Session | None = getattr(self._local, "session", None)
        if s is not None:
            return s

        s = cffi_requests.Session(impersonate=IMPERSONATE)
        s.headers.update(HEADERS)
        for c in self.seed_cookies:
            s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
        self._local.session = s
        return s

    def export_cookies(self) -> list[dict]:
        """Current cookie jar (the gateway reissues bm_s on every answer), in the
        seed format, so the session cache stays as fresh as the last request."""
        s: cffi_requests.Session | None = getattr(self._local, "session", None)
        if s is None:
            return []
        return [{"name": c.name, "value": c.value, "domain": c.domain or ".bestbuy.com", "path": c.path or "/"}
                for c in s.cookies.jar if c.value is not None]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        # Only the event-loop thread's session is reachable here; the others
        # are cleaned up when their worker thread exits. Not closing them is
        # harmless (GC closes the underlying handles).
        s: cffi_requests.Session | None = getattr(self._local, "session", None)
        if s is not None:
            try:
                s.close()
            except Exception:
                pass

    async def gql(self, op: str, query: str, variables: dict) -> dict:
        body = {"operationName": op, "query": query, "variables": variables}
        headers = {"x-requested-for-operation-name": op}

        for attempt in range(1, MAX_RETRIES + 1):
            async with self.sem:
                await asyncio.sleep(random.uniform(*JITTER))
                try:
                    r = await asyncio.to_thread(
                        self._session().post,
                        ENDPOINT,
                        json=body,
                        headers=headers,
                        timeout=TIMEOUT,
                    )
                    status, text = r.status_code, r.text
                except Exception as e:
                    status, text = 0, repr(e)
                self.calls += 1

            if status == 200:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    raise Blocked(f"{op}: 200 but non-JSON body (likely a bot-challenge page)")
            if status == 400:
                raise RuntimeError(f"{op}: HTTP 400 {text[:300]}")
            if status == 403:
                raise Blocked(f"{op}: HTTP 403 from edge")
            if status == 0 and ("curl: (92)" in text or "INTERNAL_ERROR" in text):
                # Akamai rejects bot-flagged sessions by resetting the HTTP/2
                # stream; retrying the same session never helps.
                raise Blocked(f"{op}: edge reset the HTTP/2 stream ({text[:200]})")
            if attempt == MAX_RETRIES:
                if status == 0:
                    raise Blocked(f"{op}: connection reset/failed every time ({text[:160]})")
                raise RuntimeError(f"{op}: HTTP {status} after {MAX_RETRIES} attempts")
            wait = 2.0 * attempt + random.random()
            detail = f"conn-error: {text[:160]}" if status == 0 else status
            print(f"  [{op}] HTTP {detail}, retry {attempt}/{MAX_RETRIES} in {wait:.1f}s",
                  file=sys.stderr)
            await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    # ---- prices for known SKUs
    async def prices(self, skus: list[str]) -> list[Item]:
        skus = [s for s in dict.fromkeys(skus) if re.fullmatch(r"\d{6,9}", s)]
        batches = [skus[i:i + BATCH_SIZE] for i in range(0, len(skus), BATCH_SIZE)]

        async def one(batch: list[str]) -> list[Item]:
            fields = " ".join(f's{s}: productBySkuId(skuId:"{s}"){{ {PRODUCT_FIELDS} }}' for s in batch)
            data = await self.gql("PriceBatch",
                                  f"query PriceBatch($in:ProductItemPriceInput!){{ {fields} }}",
                                  {"in": PRICE_INPUT})
            return [it for p in (data.get("data") or {}).values() if (it := Item.from_product(p))]

        return [it for chunk in await asyncio.gather(*(one(b) for b in batches)) for it in chunk]

    # ---- discovery
    @staticmethod
    def _search_vars(page_no: int, category: str, facets: list[dict]) -> dict:
        return {
            "input": {"site": "WWW", "queryType": "BROWSE", "query": f"categoryid${category}"},
            "pagination": {"pageNumber": page_no, "offset": 0},
            "filter": {"browsedCategory": category, "enableMarketplace": True,
                       "facets": facets, "deviceClass": "l", "collapse": True},
            "sort": {"sort": "Best-Selling"},
            "testing": {},
            "nd": 0,
            "in": PRICE_INPUT,
        }

    async def _search_page(self, page_no: int, category: str, facets: list[dict]):
        data = await self.gql("PlpSearch", SEARCH_QUERY, self._search_vars(page_no, category, facets))
        s = (data.get("data") or {}).get("search")
        if not s:  # past the last page -> NOT_FOUND
            return 0, [], {}
        items = [it for d in (s.get("documents") or []) if (it := Item.from_product(d.get("product")))]
        fvals = {f["facetField"]: [v["value"] for v in (f.get("values") or [])] for f in (s.get("facets") or [])}
        return s.get("numFound") or 0, items, fvals

    async def search_all(self, category: str = LAPTOPS_CATEGORY, brand: str | None = None) -> list[Item]:
        found: dict[str, Item] = {}
        base = [{"facetField": "brand_facet", "value": brand}] if brand else []
        await self._crawl(category, base, found)
        return list(found.values())

    async def _crawl(self, category: str, facets: list[dict], found: dict[str, Item]) -> None:
        label = " & ".join(f["value"] for f in facets) or "ALL"
        total, first, fvals = await self._search_page(1, category, facets)

        if total > SLICE_LIMIT:
            used = {f["facetField"] for f in facets}
            for field in SPLIT_FACETS:
                if field not in used and len(fvals.get(field, [])) > 1:
                    print(f"  [{label}] {total} > cap, splitting by {field} ({len(fvals[field])})",
                          file=sys.stderr)
                    for value in fvals[field]:
                        await self._crawl(category, facets + [{"facetField": field, "value": value}], found)
                    return
            print(f"  [{label}] {total} results, no facet left; only {MAX_PAGES} pages reachable",
                  file=sys.stderr)

        for it in first:
            found.setdefault(it.sku, it)
        last = min(MAX_PAGES, -(-total // max(len(first), 1)) + 1)
        pages = range(2, last + 1)
        results = await asyncio.gather(*(self._search_page(p, category, facets) for p in pages),
                                       return_exceptions=True)
        for p, r in zip(pages, results):
            if isinstance(r, Blocked):
                raise r
            if isinstance(r, Exception):
                print(f"  [{label}] page {p} failed: {r}", file=sys.stderr)
                continue
            for it in r[1]:
                found.setdefault(it.sku, it)
        print(f"  [{label}] numFound={total}, pages={last} -> {len(found)} unique SKUs", file=sys.stderr)


# --------------------------------------------------------------------------- io
def sku_from_url(url: str) -> str | None:
    m = re.search(r"/sku/(\d{6,9})|skuId=(\d{6,9})|/(\d{6,9})\.p", url)
    return next((g for g in m.groups() if g), None) if m else None


def save(items: list[Item], tag: str) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    fields = list(Item.__dataclass_fields__)
    with open(OUT_DIR / "history.jsonl", "a", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(asdict(it), ensure_ascii=False) + "\n")
    for path in (OUT_DIR / f"{tag}-{ts}.csv", OUT_DIR / "latest.csv"):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(asdict(i) for i in sorted(items, key=lambda x: x.price or 0))
    print(f"saved {len(items)} rows -> {OUT_DIR / 'latest.csv'} (+ history.jsonl)", file=sys.stderr)


# --------------------------------------------------------------------------- main
async def collect(a, c: BestBuyGQL) -> tuple[list[Item], str]:
    if a.url:
        sku = sku_from_url(a.url)
        return await c.prices([sku]), f"sku-{sku}"
    if a.sku:
        return await c.prices(a.sku), "skus"
    if a.refresh:
        with open(a.refresh, newline="", encoding="utf-8") as f:
            return await c.prices([r["sku"] for r in csv.DictReader(f)]), "refresh"
    brand = None if a.all else a.brand
    return await c.search_all(a.category, brand), (brand or "all").lower() + "-laptops"


async def run(a) -> int:
    if a.url and not sku_from_url(a.url):
        print("No numeric SKU in that URL. The new /product/<slug>/<ID> links need the "
              "'/sku/<number>' suffix, or pass --sku (SKU is on the product page).", file=sys.stderr)
        return 2

    # Where the session cookies come from: pasted header > cache > browser warm-up.
    seed, source = None, "browser"
    if a.cookie_file:
        seed, source = parse_cookie_header(Path(a.cookie_file).read_text(encoding="utf-8")), "file"
        print(f"  [session] {len(seed)} cookies from {a.cookie_file}", file=sys.stderr)
    elif not a.fresh and (seed := load_session()):
        source = "cache"
        print(f"  [session] reusing {len(seed)} cached cookies ({SESSION_FILE.name})", file=sys.stderr)

    while True:
        if seed is None:
            try:
                seed, accepted = await asyncio.to_thread(browser_cookies, a.headless)
            except ImportError:
                print("Browser warm-up needs Playwright: pip install playwright "
                      "(or pass --cookie-file).", file=sys.stderr)
                return 2
            source = "browser"
            if not accepted:
                print("\nBLOCKED: no browser session was accepted by the gateway; try again later.",
                      file=sys.stderr)
                return 3

        async with BestBuyGQL(a.concurrency, seed) as c:
            t0 = time.perf_counter()
            try:
                items, tag = await collect(a, c)
            except Blocked as e:
                if source == "cache":
                    # Stale cache: drop it and earn a new session in the browser, once.
                    print(f"  [session] cached session rejected ({str(e)[:60]}...); warming up again",
                          file=sys.stderr)
                    SESSION_FILE.unlink(missing_ok=True)
                    seed = None
                    continue
                print(f"\nBLOCKED: {e}\nBest Buy's edge refused this client"
                      f"{' (the pasted cookies are stale; copy fresh ones)' if source == 'file' else ''}",
                      file=sys.stderr)
                return 3

            if source != "file":
                save_session(c.export_cookies() or seed)
            print(f"{len(items)} items in {time.perf_counter() - t0:.1f}s, {c.calls} GraphQL calls",
                  file=sys.stderr)
            for it in items[:10]:
                print(f"{it.sku:>9}  ${it.price!s:>8}  (reg ${it.regular_price})  {it.name}")
            if items:
                save(items, tag)
            return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Best Buy GraphQL price scraper "
                                             "(browser only for the one-off session warm-up)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sku", nargs="+")
    g.add_argument("--url")
    g.add_argument("--brand")
    g.add_argument("--all", action="store_true")
    g.add_argument("--refresh")
    ap.add_argument("--category", default=LAPTOPS_CATEGORY)
    ap.add_argument("--concurrency", type=int, default=CONCURRENCY)
    ap.add_argument("--fresh", action="store_true",
                    help="ignore the cached session and warm up in the browser again")
    ap.add_argument("--browser-warmup", action="store_true", help=argparse.SUPPRESS)  # old flag, now the default
    # Tested: headless Chrome reaches _abck ~0~ too, but the gateway still resets
    # the stream for those cookies, so the warm-up window is visible by default.
    ap.add_argument("--headless", action="store_true",
                    help="hide the warm-up browser (the gateway currently rejects headless cookies)")
    ap.add_argument("--headful", action="store_true", help=argparse.SUPPRESS)  # old flag, now the default
    ap.add_argument("--cookie-file", help="file holding a Cookie header copied from DevTools")
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()