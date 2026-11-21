"""Primary collector: Best Buy's storefront GraphQL gateway.

This is the endpoint the bestbuy.com pages themselves call. Three operations are used:

  PlpSearch       category listing filtered by facets (brand, condition), 15 products
                  per page, at most 100 pages; larger result sets are split by facet.
  ProductDetails  aliased productBySkuId: full specification table, seller, BSIN, UPC.
  PriceBatch      aliased productBySkuId: current and regular price for known SKUs.

Session
  The gateway only answers a session whose Akamai cookies were earned by a real
  browser running the site's scripts, so the first run opens Chrome once
  (Playwright), waits until the gateway answers from inside the page and hands the
  cookies to curl_cffi. Accepted cookies are cached in `.bestbuy_session.json` and
  reused; the browser opens again only when the gateway rejects the cached session.
  All scraping itself is plain HTTP with Chrome TLS/HTTP2 impersonation.

Politeness
  Requests are paced at a fixed minimum interval (REQUEST_DELAY_SECONDS), retried
  with backoff on transient errors, and the run stops on an edge refusal instead of
  escalating.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from curl_cffi import requests as cffi_requests

from .. import config

SOURCE = "bestbuy_graphql"
ENDPOINT = config.BESTBUY_GRAPHQL_URL
HOME_URL = "https://www.bestbuy.com/"
LAPTOPS_CATEGORY = "pcmcat138500050001"  # "All Laptops"

CONCURRENCY = 2
DETAIL_BATCH = 10      # spec tables are ~150 rows each
PRICE_BATCH = 20
MAX_RETRIES = 4
TIMEOUT = 30.0
PAGE_SIZE = 15         # fixed by the gateway
MAX_PAGES = 100        # the listing stops answering after page 100
SLICE_LIMIT = 1450     # split a facet slice before it reaches 100 pages x 15
SPLIT_FACETS = ["brand_facet", "currentprice_facet", "systemmemoryrange_facet",
                "laptopscreensizerange_facet", "totalstoragecapacityrange_facet", "processorbrand_facet"]

# Cookies of the last session the gateway accepted (treat like a login cookie file).
SESSION_FILE = config.ROOT / ".bestbuy_session.json"

PRICE_INPUT = {"salesChannel": "LargeView", "context": "plp"}

# curl_cffi sets User-Agent / sec-ch-ua / the HTTP/2 preamble itself; these are the
# GraphQL-specific extras the gateway expects.
HEADERS = {
    "x-dynatrace": "",
    "accept": "application/graphql-response+json,application/json;q=0.9",
    "content-type": "application/json",
    "x-client-id": "pdp-web",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}
IMPERSONATE = "chrome"

PRICE_FIELDS = "skuId brand name{short} seller{classification} openBoxCondition price(input:$in){ customerPrice regularPrice }"
LISTING_FIELDS = "skuId brand bsin upc openBoxCondition name{short} seller{classification} price(input:$in){ customerPrice regularPrice }"
DETAIL_FIELDS = (LISTING_FIELDS
                 + " hierarchy{ bbypres{ primary id categoryDetail{ name } } }"
                 + " specificationGroups{ name specifications{ displayName value } }")

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
""" % LISTING_FIELDS

T = TypeVar("T")


class Blocked(RuntimeError):
    """The edge (Akamai) refused the request rather than GraphQL answering it."""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- model
_CONDITION_PATTERNS = (
    ("refurbished", re.compile(r"\b(refurbished|renewed|recertified|remanufactured|geek squad certified)\b", re.I)),
    ("open-box", re.compile(r"\bopen[- ]box\b", re.I)),
    ("pre-owned", re.compile(r"\b(pre[- ]owned|used)\b", re.I)),
)
_CONDITION_FACETS = {"new": "New", "open-box": "Open-Box", "refurbished": "Refurbished"}


def infer_condition(title: str | None, open_box_condition: str | None) -> str:
    """Condition of an offer when it did not come from a condition-filtered listing."""
    if open_box_condition:
        return "open-box"
    for name, pattern in _CONDITION_PATTERNS:
        if title and pattern.search(title):
            return name
    return "new"


def product_url(sku: str) -> str:
    return f"https://www.bestbuy.com/site/{sku}.p?skuId={sku}"


def sku_from_url(url: str) -> str | None:
    """Numeric SKU from a bestbuy.com URL, or None (new-style /product/<slug>/<BSIN> links carry none)."""
    m = re.search(r"/sku/(\d{6,9})|[?&]skuId=(\d{6,9})|/(\d{6,9})\.p(?:[?/#]|$)", url)
    return next((g for g in m.groups() if g), None) if m else None


def bsin_from_url(url: str) -> str | None:
    m = re.search(r"/product/[^/?#]+/([A-Za-z0-9]{8,12})(?:[/?#]|$)", url)
    return m.group(1).upper() if m else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Product:
    """One Best Buy offer (SKU) as the gateway describes it."""
    sku: str
    title: str | None
    brand: str | None
    bsin: str | None = None
    upc: str | None = None
    seller_class: str | None = None          # 1P (sold by Best Buy) | 3P (Marketplace seller)
    open_box_condition: str | None = None
    condition: str | None = None             # new | open-box | refurbished | pre-owned
    price: float | None = None               # customerPrice: what the shopper pays now
    list_price: float | None = None          # regularPrice: list / "Comp. Value"
    url: str = ""
    categories: list[str] = field(default_factory=list)
    raw_specs: dict[str, dict[str, str]] | None = None   # {group: {display name: value}}, untouched
    rank: int | None = None                  # position in Best Buy's best-selling order, when crawled
    scraped_at: str = ""

    @classmethod
    def from_gql(cls, p: dict | None, *, condition: str | None = None, rank: int | None = None) -> "Product | None":
        if not p or not p.get("skuId"):
            return None
        pr = p.get("price") or {}
        title = (p.get("name") or {}).get("short")
        groups = p.get("specificationGroups")
        specs = None
        if groups is not None:
            specs = {}
            for g in groups or []:
                rows = specs.setdefault(g.get("name") or "", {})
                for s in g.get("specifications") or []:
                    if s.get("displayName") and s.get("value") is not None:
                        rows[s["displayName"]] = s["value"]
        cats = [(h.get("categoryDetail") or {}).get("name")
                for h in ((p.get("hierarchy") or {}).get("bbypres") or [])]
        return cls(
            sku=str(p["skuId"]),
            title=title,
            brand=p.get("brand"),
            bsin=p.get("bsin"),
            upc=p.get("upc"),
            seller_class=(p.get("seller") or {}).get("classification"),
            open_box_condition=p.get("openBoxCondition"),
            condition=condition or infer_condition(title, p.get("openBoxCondition")),
            price=pr.get("customerPrice"),
            list_price=pr.get("regularPrice"),
            url=product_url(str(p["skuId"])),
            categories=[c for c in cats if c],
            raw_specs=specs,
            rank=rank,
            scraped_at=_now(),
        )

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- session
def abck_state(abck: str | None) -> str:
    """'0' = validated by Akamai's JS sensor, '-1' = issued but never validated."""
    if not abck:
        return "missing"
    m = re.match(r"[0-9A-Fa-f]+~(-?\d+)~", abck)
    return m.group(1) if m else "unparsed"


def parse_cookie_header(header: str) -> list[dict]:
    """'a=1; b=2' (as copied from DevTools) -> cookie dicts."""
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
        SESSION_FILE.write_text(json.dumps({"saved_at": _now(), "cookies": cookies}, indent=1), encoding="utf-8")
    except OSError as e:
        log(f"  [session] could not write {SESSION_FILE.name}: {e}")


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
        # _abck ~0~ is neither sufficient nor strictly necessary: about half of the
        # ~0~ sessions still had their gateway streams reset in testing. So give the
        # sensor a few seconds, then probe the gateway from inside the page and only
        # hand over cookies from a session the edge actually answers; otherwise
        # start a fresh context and try again.
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
            log(f"  [warmup:browser] attempt {attempt}/{attempts}: _abck state={state}, "
                f"{len(cookies)} cookies, gateway probe={probe}")
            ctx.close()
            if accepted:
                break
        browser.close()

    if not accepted:
        log("  [warmup:browser] no session was accepted by the gateway"
            f"{' (headless sessions were never accepted in testing; drop --headless)' if headless else ''}")
    return [{k: c[k] for k in ("name", "value", "domain", "path")} for c in cookies], accepted


# --------------------------------------------------------------------------- client
class BestBuyGQL:
    def __init__(self, seed_cookies: list[dict], *, concurrency: int = CONCURRENCY,
                 delay: float | None = None):
        self.sem = asyncio.Semaphore(concurrency)
        self.delay = config.REQUEST_DELAY_SECONDS if delay is None else delay
        self.calls = 0
        self.bundles_skipped = 0
        self.seed_cookies = seed_cookies
        self._pace_lock = asyncio.Lock()
        self._next_at = 0.0
        # curl_cffi Session objects are not documented as safe for concurrent use
        # from multiple threads, so each worker thread gets its own session.
        self._local = threading.local()
        self._sessions: list[cffi_requests.Session] = []
        self._sessions_lock = threading.Lock()

    def _session(self) -> cffi_requests.Session:
        s: cffi_requests.Session | None = getattr(self._local, "session", None)
        if s is None:
            s = cffi_requests.Session(impersonate=IMPERSONATE)
            s.headers.update(HEADERS)
            for c in self.seed_cookies:
                s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
            self._local.session = s
            with self._sessions_lock:
                self._sessions.append(s)
        return s

    def export_cookies(self) -> list[dict]:
        """Cookie jar of the most recently created HTTP session (the gateway reissues
        bm_s on every answer), in the seed format, to keep the cache fresh."""
        with self._sessions_lock:
            sessions = list(self._sessions)
        for s in reversed(sessions):
            jar = [{"name": c.name, "value": c.value, "domain": c.domain or ".bestbuy.com", "path": c.path or "/"}
                   for c in s.cookies.jar if c.value is not None]
            if jar:
                return jar
        return []

    def close(self) -> None:
        with self._sessions_lock:
            sessions, self._sessions = self._sessions, []
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass

    async def _pace(self) -> None:
        """Fixed minimum interval between request starts, across all workers."""
        async with self._pace_lock:
            now = time.monotonic()
            if self._next_at > now:
                await asyncio.sleep(self._next_at - now)
            self._next_at = max(now, self._next_at) + self.delay * random.uniform(1.0, 1.3)

    async def gql(self, op: str, query: str, variables: dict) -> dict:
        body = {"operationName": op, "query": query, "variables": variables}
        headers = {"x-requested-for-operation-name": op}

        for attempt in range(1, MAX_RETRIES + 1):
            async with self.sem:
                await self._pace()
                try:
                    r = await asyncio.to_thread(self._session().post, ENDPOINT, json=body,
                                                headers=headers, timeout=TIMEOUT)
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
            log(f"  [{op}] HTTP {detail}, retry {attempt}/{MAX_RETRIES} in {wait:.1f}s")
            await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    # ---- known SKUs
    async def _by_sku(self, op: str, fields: str, skus: list[str], batch_size: int) -> tuple[list[Product], list[str]]:
        """Aliased productBySkuId. Returns (products, skus the gateway did not return)."""
        skus = [s for s in dict.fromkeys(str(s).strip() for s in skus) if re.fullmatch(r"\d{6,9}", s)]
        batches = [skus[i:i + batch_size] for i in range(0, len(skus), batch_size)]

        async def one(batch: list[str]) -> list[Product]:
            selection = " ".join(f's{s}: productBySkuId(skuId:"{s}"){{ {fields} }}' for s in batch)
            data = await self.gql(op, f"query {op}($in:ProductItemPriceInput!){{ {selection} }}", {"in": PRICE_INPUT})
            return [p for raw in (data.get("data") or {}).values() if (p := Product.from_gql(raw))]

        found = [p for chunk in await asyncio.gather(*(one(b) for b in batches)) for p in chunk]
        got = {p.sku for p in found}
        return found, [s for s in skus if s not in got]

    async def prices(self, skus: list[str]) -> tuple[list[Product], list[str]]:
        """Current price for known SKUs. Returns (priced products, missing SKUs)."""
        return await self._by_sku("PriceBatch", PRICE_FIELDS, skus, PRICE_BATCH)

    async def details(self, skus: list[str]) -> tuple[list[Product], list[str]]:
        """Full product record including the specification table."""
        return await self._by_sku("ProductDetails", DETAIL_FIELDS, skus, DETAIL_BATCH)

    # ---- discovery
    @staticmethod
    def _search_vars(page_no: int, category: str, facets: list[dict], marketplace: bool) -> dict:
        return {
            "input": {"site": "WWW", "queryType": "BROWSE", "query": f"categoryid${category}"},
            "pagination": {"pageNumber": page_no, "offset": 0},
            # collapse=False lists every SKU; True would fold colour variants into one result.
            "filter": {"browsedCategory": category, "enableMarketplace": marketplace,
                       "facets": facets, "deviceClass": "l", "collapse": False},
            "sort": {"sort": "Best-Selling"},
            "testing": {},
            "nd": 0,
            "in": PRICE_INPUT,
        }

    async def _search_page(self, page_no: int, category: str, facets: list[dict], marketplace: bool):
        data = await self.gql("PlpSearch", SEARCH_QUERY, self._search_vars(page_no, category, facets, marketplace))
        s = (data.get("data") or {}).get("search")
        if not s:  # past the last page -> NOT_FOUND
            return 0, [], {}
        docs = s.get("documents") or []
        # numFound also counts package bundles ("SearchCombo"): they carry no single
        # product and are not tracked, so a brand can list fewer SKUs than numFound.
        products = [d["product"] for d in docs if d.get("product")]
        self.bundles_skipped += len(docs) - len(products)
        fvals = {f["facetField"]: [v["value"] for v in (f.get("values") or [])] for f in (s.get("facets") or [])}
        return s.get("numFound") or 0, products, fvals

    async def discover(self, brand: str | None = None, *, condition: str | None = "new",
                       marketplace: bool = False, category: str = LAPTOPS_CATEGORY,
                       limit: int | None = None) -> list[Product]:
        """Every laptop SKU in the category matching the filters, in best-selling order.

        brand        Best Buy brand facet value (None = all brands)
        condition    'new' | 'open-box' | 'refurbished' | None (any)
        marketplace  False = offers sold by Best Buy only; True = Marketplace sellers too
        limit        stop after the first N results (Best Buy's best-selling order)
        """
        facets = []
        if brand:
            facets.append({"facetField": "brand_facet", "value": brand})
        if condition:
            facets.append({"facetField": "condition_facet", "value": _CONDITION_FACETS[condition]})
        found: dict[str, Product] = {}
        await self._crawl(category, facets, marketplace, condition, found, limit)
        products = list(found.values())
        return products[:limit] if limit else products

    async def _crawl(self, category: str, facets: list[dict], marketplace: bool, condition: str | None,
                     found: dict[str, Product], limit: int | None) -> None:
        label = " & ".join(f["value"] for f in facets) or "ALL"
        total, first, fvals = await self._search_page(1, category, facets, marketplace)

        if total > SLICE_LIMIT and not limit:
            used = {f["facetField"] for f in facets}
            for fld in SPLIT_FACETS:
                if fld not in used and len(fvals.get(fld, [])) > 1:
                    log(f"  [{label}] {total} > cap, splitting by {fld} ({len(fvals[fld])})")
                    for value in fvals[fld]:
                        await self._crawl(category, facets + [{"facetField": fld, "value": value}],
                                          marketplace, condition, found, limit)
                    return
            log(f"  [{label}] {total} results, no facet left; only {MAX_PAGES} pages reachable")

        def add(raw_products: list[dict]) -> None:
            for raw in raw_products:
                p = Product.from_gql(raw, condition=condition, rank=len(found) + 1)
                if p:
                    found.setdefault(p.sku, p)

        add(first)
        want = min(total, limit) if limit else total
        last = min(MAX_PAGES, -(-want // PAGE_SIZE))
        for start in range(2, last + 1, CONCURRENCY * 2):
            pages = range(start, min(start + CONCURRENCY * 2, last + 1))
            results = await asyncio.gather(*(self._search_page(p, category, facets, marketplace) for p in pages),
                                           return_exceptions=True)
            for p, r in zip(pages, results):
                if isinstance(r, Blocked):
                    raise r
                if isinstance(r, Exception):
                    log(f"  [{label}] page {p} failed: {r}")
                    continue
                add(r[1])
        log(f"  [{label}] numFound={total}, pages={last} -> {len(found)} unique SKUs"
            + (f" ({self.bundles_skipped} package bundles skipped so far)" if self.bundles_skipped else ""))


# --------------------------------------------------------------------------- session runner
async def run_with_session(job: Callable[[BestBuyGQL], Awaitable[T]], *, fresh: bool = False,
                           headless: bool = False, cookie_file: str | None = None,
                           concurrency: int = CONCURRENCY, delay: float | None = None) -> T:
    """Run `job(client)` with a gateway-accepted session.

    Cookie source, in order: a pasted Cookie header (cookie_file), the cached
    session, a browser warm-up. A rejected cached session is dropped and replaced by
    one browser warm-up; any other refusal raises Blocked (never escalates).
    """
    seed, source = None, "browser"
    if cookie_file:
        seed, source = parse_cookie_header(Path(cookie_file).read_text(encoding="utf-8")), "file"
        log(f"  [session] {len(seed)} cookies from {cookie_file}")
    elif not fresh and (seed := load_session()):
        source = "cache"
        log(f"  [session] reusing {len(seed)} cached cookies ({SESSION_FILE.name})")

    while True:
        if seed is None:
            try:
                seed, accepted = await asyncio.to_thread(browser_cookies, headless)
            except ImportError as e:
                raise Blocked("browser warm-up needs Playwright: pip install playwright "
                              "(or pass --cookie-file)") from e
            source = "browser"
            if not accepted:
                raise Blocked("no browser session was accepted by the gateway; try again later")

        client = BestBuyGQL(seed, concurrency=concurrency, delay=delay)
        try:
            result = await job(client)
            # Only a session the gateway kept answering is worth caching.
            if source != "file" and client.calls:
                save_session(client.export_cookies() or seed)
            return result
        except Blocked as e:
            if source != "cache":
                raise
            log(f"  [session] cached session rejected ({str(e)[:60]}...); warming up in the browser")
            SESSION_FILE.unlink(missing_ok=True)
            seed = None
        finally:
            client.close()


def add_session_args(ap) -> None:
    """CLI flags shared by every script that talks to Best Buy."""
    ap.add_argument("--fresh", action="store_true",
                    help="ignore the cached session and warm up in the browser again")
    ap.add_argument("--headless", action="store_true",
                    help="hide the warm-up browser (the gateway currently rejects headless cookies)")
    ap.add_argument("--cookie-file", help="file holding a Cookie header copied from DevTools")


def session_kwargs(args) -> dict:
    return {"fresh": args.fresh, "headless": args.headless, "cookie_file": args.cookie_file}
