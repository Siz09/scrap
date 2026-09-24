"""Retailer-agnostic adapter for sites that are not Shopify/WooCommerce/Daraz.

Most stores embed schema.org Product JSON-LD for Google Shopping. That gives name,
brand, price, currency, stock and rating on almost any shop with zero per-site code.
Review sites embed schema.org Review, which yields an expert score. Spec tables are
harvested heuristically (<table> th/td rows, <dl> dt/dd pairs, 'Label: value' lines).

Product URLs come from the site's XML sitemap by default (no selectors needed), or
from listing pages + a CSS selector if configured.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from ..models import Offer, Product
from ..normalize import finalize, parse_label_lines, parse_price, parse_variant
from .backends import RateLimited, NotFound
from .base import Fetcher, Source, _looks_like_js_shell, text_of

log = logging.getLogger(__name__)


@dataclass
class SiteConfig:
    name: str
    base_url: str = ""
    start_urls: list[str] = field(default_factory=list)
    product_link_css: str | None = None       # if unset, discover via sitemap
    next_page_css: str | None = None
    max_pages: int = 1
    url_include: str = r"/(product|products|p|item|mobile|laptop|phone)s?[/-]"  # regex on sitemap URLs
    url_exclude: str = r"/(tag|category|categories|collections?|brand|blog|page)/"
    keywords: list[str] = field(default_factory=list)  # optional extra filter on sitemap URLs
    fetch_mode: str = "static"
    category_hint: str | None = None
    region: str = "np"
    currency: str | None = None
    official: bool | None = None
    price_from_text: bool = False   # regex "Rs. 1,23,456" in page text when JSON-LD has no price
    # Optional overrides when JSON-LD is missing or wrong on this site.
    name_css: str | None = None
    price_css: str | None = None
    spec_row_css: str | None = None     # each matched row yields (label, value)
    spec_label_css: str = "th, dt, .label"
    spec_value_css: str = "td, dd, .value"
    breadcrumb_css: str = "nav[aria-label*='readcrumb'] a, .breadcrumb a"
    # Whole-site crawl (sites without a product sitemap, or crawl_site=True): how many listing
    # pages (categories, brands, offers, page 2, 3 ...) to walk per run.
    browse_pages: int = 400
    crawl_site: bool = False        # walk the site's pages even when it has a product sitemap


def _walk_jsonld(obj) -> Iterator[dict]:
    if isinstance(obj, list):
        for o in obj:
            yield from _walk_jsonld(o)
    elif isinstance(obj, dict):
        if "@graph" in obj:
            yield from _walk_jsonld(obj["@graph"])
        yield obj


def _is_type(obj: dict, *names: str) -> bool:
    t = obj.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(n in types for n in names)


def jsonld_objects(page) -> list[dict]:
    out = []
    for script in page.css("script[type='application/ld+json']::text").getall():
        try:
            out.extend(_walk_jsonld(json.loads(script)))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def extract_jsonld_product(page) -> dict | None:
    for obj in jsonld_objects(page):
        if _is_type(obj, "Product", "ProductGroup"):
            return obj
    return None


def extract_jsonld_review(page) -> dict | None:
    for obj in jsonld_objects(page):
        if _is_type(obj, "Review") and isinstance(obj.get("reviewRating"), dict):
            return obj
        review = obj.get("review") if isinstance(obj, dict) else None
        if isinstance(review, dict) and isinstance(review.get("reviewRating"), dict) and _is_type(review, "Review"):
            return {**review, "itemReviewed": review.get("itemReviewed") or obj}
    return None


_NAME_KEYS = ("name", "title", "productName", "product_name")
_PRICE_KEYS = ("salePrice", "sellingPrice", "specialPrice", "discountedPrice", "discountPrice", "finalPrice",
               "final_price", "selling_price", "sale_price", "offerPrice", "price")
_ORIGINAL_KEYS = ("mrp", "regularPrice", "regular_price", "originalPrice", "compareAtPrice", "compare_at_price",
                  "listPrice", "marketPrice")


def _embedded_json(page) -> list:
    """JSON that JavaScript-built stores embed in the page: Next.js __NEXT_DATA__, Nuxt, generic state."""
    out = []
    for script in page.css("script#__NEXT_DATA__::text, script[type='application/json']::text").getall():
        try:
            out.append(json.loads(script))
        except (ValueError, TypeError):
            continue
    for script in page.css("script:not([src])::text").getall():
        m = re.search(r"(?:__NUXT__|__INITIAL_STATE__|__PRELOADED_STATE__)\s*=\s*(\{.*\})\s*;?\s*$", script, re.S)
        if m:
            try:
                out.append(json.loads(m.group(1)))
            except ValueError:
                pass
    return out


def extract_embedded_product(page, hint_name: str = "") -> dict | None:
    """The best product-like object in embedded JSON: has a name and a price.
    Prefers the one whose name matches the page title/h1 (not a 'related products' entry)."""
    best, best_score = None, -1
    hint = set(re.findall(r"\w+", hint_name.lower()))

    def walk(node, depth=0):
        nonlocal best, best_score
        if depth > 12:
            return
        if isinstance(node, dict):
            name = next((node[k] for k in _NAME_KEYS if isinstance(node.get(k), str) and len(node[k]) > 3), None)
            price = next((parse_price(node[k]) for k in _PRICE_KEYS if node.get(k) not in (None, "", 0)), None)
            if name and price:
                words = set(re.findall(r"\w+", name.lower()))
                score = len(words & hint) + (5 if any(k in node for k in ("specifications", "attributes", "specs")) else 0)
                if score > best_score:
                    best, best_score = node, score
            for v in node.values():
                walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node[:200]:
                walk(v, depth + 1)

    for blob in _embedded_json(page):
        walk(blob)
    if not best:
        return None
    specs: dict[str, str] = {}
    for key in ("specifications", "attributes", "specs", "features"):
        items = best.get(key)
        if isinstance(items, dict):
            specs.update({str(k): str(v) for k, v in items.items() if isinstance(v, (str, int, float))})
        elif isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    label = it.get("name") or it.get("key") or it.get("label") or it.get("title")
                    value = it.get("value") or it.get("values") or it.get("option")
                    if isinstance(value, list):
                        value = ", ".join(str(v) for v in value)
                    if label and value:
                        specs[str(label)] = str(value)
    brand = best.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name") or brand.get("title")
    return {
        "name": next(best[k] for k in _NAME_KEYS if isinstance(best.get(k), str) and len(best[k]) > 3),
        "price": next((parse_price(best[k]) for k in _PRICE_KEYS if best.get(k) not in (None, "", 0)), None),
        "original": next((parse_price(best[k]) for k in _ORIGINAL_KEYS if best.get(k) not in (None, "", 0)), None),
        "brand": brand if isinstance(brand, str) else None,
        "specs": specs,
    }


def extract_meta_price(page) -> tuple[float | None, str | None]:
    """Price from Open Graph / Facebook product tags or schema.org microdata."""
    for sel in ("meta[property='product:price:amount']::attr(content)",
                "meta[property='og:price:amount']::attr(content)",
                "[itemprop='price']::attr(content)", "[itemprop='price']::text"):
        v = page.css(sel).get()
        if v and parse_price(v):
            cur = (page.css("meta[property='product:price:currency']::attr(content)").get()
                   or page.css("[itemprop='priceCurrency']::attr(content)").get())
            return parse_price(v), cur
    return None, None


def extract_spec_rows(page, cfg: SiteConfig) -> dict[str, str]:
    raw: dict[str, str] = {}
    rows = page.css(cfg.spec_row_css) if cfg.spec_row_css else page.css("table tr")
    for row in rows:
        label = text_of(row.css(cfg.spec_label_css).first)
        value = text_of(row.css(cfg.spec_value_css).first)
        if not cfg.spec_row_css and not row.css("th"):
            cells = row.css("td")  # two-column tables without <th>: <td>Label</td><td>Value</td>
            label, value = (text_of(cells[0]), text_of(cells[1])) if len(cells) == 2 else ("", "")
        if label and value and len(label) < 60:
            raw.setdefault(label.rstrip(":"), value)
    if not cfg.spec_row_css:
        for dl in page.css("dl"):
            for dt, dd in zip(dl.css("dt"), dl.css("dd")):
                label, value = text_of(dt), text_of(dd)
                if label and value and len(label) < 60:
                    raw.setdefault(label.rstrip(":"), value)
    return raw


_NPR_TEXT = re.compile(r"(?:price[^.\n]{0,40}?)?(?:rs\.?|npr|nrs\.?|रु\.?)\s*([\d,]{4,}(?:\.\d+)?)", re.I)


def price_from_text(text: str) -> float | None:
    """'Samsung Galaxy A56 price in Nepal: Rs. 57,999' -> 57999. First NPR amount after 'price'."""
    m = re.search(r"price[^\n]{0,60}?(?:rs\.?|npr|nrs\.?|रु\.?)\s*([\d,]{4,})", text, re.I) or _NPR_TEXT.search(text)
    return parse_price(m.group(1)) if m else None


# --- sitemap discovery -----------------------------------------------------

_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", re.S | re.I)


def sitemap_urls(fetcher: Fetcher, base_url: str, limit: int = 5000) -> Iterator[str]:
    """Product URLs from robots.txt 'Sitemap:' entries or common sitemap paths, recursing indexes."""
    root = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    queue: list[str] = []
    try:
        robots = fetcher.get(root + "/robots.txt")
        queue += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots.body.decode(errors="replace")
                            if isinstance(robots.body, bytes) else str(robots.body))
    except RateLimited:
        raise                    # the site limits us: stop it for this run
    except Exception as e:
        log.info("no robots.txt sitemap for %s: %s", root, e)
    queue += [root + p for p in ("/sitemap.xml", "/sitemap_index.xml", "/product-sitemap.xml", "/sitemap_products_1.xml")]
    seen_maps: set[str] = set()
    n = 0
    while queue and n < limit:
        sm = queue.pop(0)
        if sm in seen_maps:
            continue
        seen_maps.add(sm)
        try:
            page = fetcher.get(sm)
        except RateLimited:
            raise                    # the site limits us: stop it for this run
        except Exception:
            continue
        body = page.body.decode(errors="replace") if isinstance(page.body, bytes) else str(page.body)
        locs = _LOC.findall(body)
        # Sitemap index: follow child sitemaps, product ones first.
        children = [u for u in locs if re.search(r"sitemap[^/]*\.xml", u, re.I)]
        if children:
            children.sort(key=lambda u: 0 if "product" in u.lower() else 1)
            queue = children + queue
            continue
        for u in locs:
            n += 1
            yield u
            if n >= limit:
                return


class GenericSource(Source):
    def __init__(self, cfg: SiteConfig):
        self.cfg = cfg
        self.name = cfg.name
        self.fetch_mode = cfg.fetch_mode      # product pages
        self.listing_mode = cfg.fetch_mode    # category / listing pages (often JS-built even when
                                              # product pages carry their data in plain HTML: hukut)
        self._browser_wins = 0
        self.deadline: float | None = None    # time.monotonic() after which a crawl stops (checks)
        self._hints: dict[str, str] = {}   # product url -> words of the listing it was found on

    def _wanted(self, url: str) -> bool:
        if self.cfg.url_exclude and re.search(self.cfg.url_exclude, url, re.I):
            return False
        if self.cfg.url_include and not re.search(self.cfg.url_include, url, re.I):
            return False
        return not self.cfg.keywords or any(k.lower() in url.lower() for k in self.cfg.keywords)

    def discover(self, fetcher: Fetcher, **_) -> Iterator[str]:
        """Product URLs from the sitemap, or (no sitemap) from walking the site's listing pages."""
        seen: set[str] = set()
        if not self.cfg.product_link_css:
            for u in sitemap_urls(fetcher, self._base()):
                if u not in seen and self._wanted(u) and self._looks_like_product(u):
                    seen.add(u)
                    yield u
            if not seen:
                yield from self._browse(fetcher, self._base())
            return
        for url in self.cfg.start_urls:
            for _ in range(self.cfg.max_pages):
                page = fetcher.get(url)
                for href in page.css(self.cfg.product_link_css).getall():
                    full = page.urljoin(href).split("#")[0]
                    if full not in seen:
                        seen.add(full)
                        yield full
                nxt = page.css(self.cfg.next_page_css).get() if self.cfg.next_page_css else None
                if not nxt:
                    break
                url = page.urljoin(nxt)

    def _base(self) -> str:
        return self.cfg.base_url or (self.cfg.start_urls[0] if self.cfg.start_urls else "")

    def crawl(self, fetcher: Fetcher, limit: int = 20, **opts) -> Iterator[Product]:
        """Everything the site lists: products from the sitemap, then (no sitemap, or crawl_site)
        a walk over every category / brand / offer / next-page listing, following links found on
        product pages too (related products, breadcrumbs)."""
        if self.cfg.product_link_css:
            yield from super().crawl(fetcher, limit, **opts)
            return
        done: set[str] = set()
        seeds: list[str] = []      # sitemap entries that are category / brand pages, not products
        n = 0
        for url in sitemap_urls(fetcher, self._base()):
            if n >= limit or (self.deadline and time.monotonic() > self.deadline):
                return
            if url in done or not self._wanted(url):
                continue
            if not self._looks_like_product(url):
                seeds.append(url)  # itti's sitemap: /laptops-by-brands/asus-laptop-nepal/zenbook-series
                continue
            done.add(url)
            product = self._product(fetcher, url)
            if product:
                n += 1
                yield product
            else:
                seeds.append(url)  # a category page after all: walk it for its products
        if n and not self.cfg.crawl_site:
            return
        # No usable product sitemap: walk the site, starting from the category pages it listed.
        for product in self._site_crawl(fetcher, limit - n, skip=done, seeds=seeds):
            yield product

    def _is_listing(self, page) -> bool:
        """Many product links and no product data of its own: a category page, whatever its URL."""
        if extract_jsonld_product(page):
            return False
        host = urlparse(page.url).netloc
        return sum(1 for u in self._links(page, host) if self._looks_like_product(u)) >= 6

    def _product(self, fetcher: Fetcher, url: str, page=None) -> Product | None:
        try:
            page = page or fetcher.get(url, mode=self.fetch_mode)
            if self._is_listing(page):
                return None
            product = self.parse(page)
            if product is None and self.fetch_mode != "dynamic" and _looks_like_js_shell(page):
                rendered = fetcher.get(url, mode="dynamic")
                product = None if self._is_listing(rendered) else self.parse(rendered)
                if product:
                    self._browser_wins += 1
                    if self._browser_wins >= 2:     # this site's product pages need the browser
                        log.info("[%s] product pages need a browser; rendering them from now on", self.name)
                        self.fetch_mode = "dynamic"
            return product
        except NotFound:
            return None
        except RateLimited:
            raise                    # the site limits us: stop it for this run
        except Exception as e:
            log.warning("[%s] failed %s: %s", self.name, url, e)
            return None

    _PAGE_PARAMS = {"page", "p", "pg", "paged", "pagenumber", "page_no", "pageno", "offset", "start"}

    def _canonical(self, url: str, product: bool) -> str:
        """One URL per page: products lose their query string; listings keep only pagination
        (?page=2), not sort/filter variants that would multiply the same listing endlessly."""
        u = urlparse(url.split("#")[0])
        if product:
            query = ""
        else:
            keep = [kv for kv in u.query.split("&") if kv and kv.split("=")[0].lower() in self._PAGE_PARAMS]
            query = "&".join(sorted(keep))
        path = u.path.rstrip("/") or "/"
        return f"{u.scheme}://{u.netloc}{path}" + (f"?{query}" if query else "")

    def _site_crawl(self, fetcher: Fetcher, limit: int, skip: set[str] = frozenset(),
                    seeds: list[str] = ()) -> Iterator[Product]:
        from collections import deque
        host = urlparse(self._base()).netloc
        listings = deque(dict.fromkeys(self._canonical(u, False)
                                       for u in [*self.cfg.start_urls, *seeds, self._base()] if u))
        products: deque[str] = deque()
        queued = set(listings) | set(skip)
        walked = n = 0
        while (products or listings) and n < limit:
            if self.deadline and time.monotonic() > self.deadline:
                log.info("[%s] time limit reached after %d listing pages", self.name, walked)
                break
            if products:
                url, is_product = products.popleft(), True
            else:
                if walked >= self.cfg.browse_pages:
                    log.info("[%s] stopped after %d listing pages (browse_pages)", self.name, walked)
                    break
                url, is_product = listings.popleft(), False
            try:
                if is_product:
                    page = fetcher.get(url, mode=self.fetch_mode)
                else:
                    page = self._page(fetcher, url)
                    walked += 1
            except NotFound:
                continue
            except RateLimited:
                raise                    # the site limits us: stop it for this run
            except Exception as e:
                log.info("[%s] %s: %s", self.name, url, e)
                continue
            if is_product or extract_jsonld_product(page):   # a product with a short URL (/iphone-air)
                product = self._product(fetcher, url, page)
                if product:
                    n += 1
                    yield product
            # Every page, product pages included, can lead to more of the site.
            listing_path = urlparse(url).path
            for link in self._links(page, host) + self._embedded_links(page, host):
                if self._looks_like_product(link):
                    link = self._canonical(link, True)
                    if link not in queued:
                        queued.add(link)
                        products.append(link)
                        if not is_product:
                            self._hints[link] = listing_path.replace("-", " ").replace("/", " ")
                else:
                    link = self._canonical(link, False)
                    slug_words = set(re.split(r"[-_/]+", urlparse(link).path.lower()))
                    if slug_words & self._NOT_PRODUCT:
                        continue            # about / terms / contact pages lead nowhere useful
                    if link not in queued:
                        queued.add(link)
                        # Pagination and category pages first, so products start flowing early.
                        if re.search(r"[?&](page|p|pg|paged)=|/page/\d", link) or self._CATEGORY.search(link):
                            listings.appendleft(link)
                        else:
                            listings.append(link)
        log.info("[%s] site crawl: %d listing pages walked, %d product pages queued, %d products",
                 self.name, walked, len([u for u in queued if self._looks_like_product(u)]), n)

    _CATEGORY = re.compile(r"mobile|phone|smartphone|laptop|notebook|tablet|ipad|watch|wearable|power-?bank|"
                           r"earbud|headphone|audio|charger|accessor|gadget|electronic", re.I)
    _ASSET = re.compile(r"\.(jpe?g|png|webp|gif|svg|css|js|pdf|zip|xml)(\?|$)|/(cart|checkout|account|login|"
                        r"register|wishlist|compare|search|blog|news|about|contact|faq|policy|terms)(/|$|\?)", re.I)

    def _links(self, page, host: str) -> list[str]:
        out = []
        for href in page.css("a::attr(href)").getall():
            full = page.urljoin(href).split("#")[0]
            u = urlparse(full)
            if u.netloc == host and u.path not in ("", "/") and not self._ASSET.search(full):
                out.append(full)
        return list(dict.fromkeys(out))

    _NOT_PRODUCT = {"about", "terms", "conditions", "condition", "privacy", "policy", "policies", "career",
                    "careers", "contact", "faq", "faqs", "warranty", "returns", "refund", "shipping",
                    "delivery", "locations", "branches", "login", "register", "account", "blog", "news"}

    _FILLER = {"price", "prices", "in", "nepal", "np", "best", "buy", "online", "latest", "new", "2024", "2025",
               "2026", "2027"}
    _PLURALS = {"laptops", "notebooks", "phones", "smartphones", "mobiles", "tablets", "monitors", "watches",
                "smartwatches", "earbuds", "headphones", "speakers", "cameras", "accessories", "desktops",
                "computers", "printers", "routers", "chargers", "cables", "cases", "tvs", "televisions",
                "consoles", "gadgets", "products", "deals", "offers", "brands", "collection",
                "collections", "category", "categories"}

    def _looks_like_product(self, url: str) -> bool:
        # Product pages have a long, specific slug: /samsung-galaxy-a56-5g-8gb-256gb
        parts = urlparse(url).path.strip("/").split("/")
        slug = parts[-1]
        words = set(re.split(r"[-_]+", slug.lower()))
        if len(parts) >= 2 and parts[-2].lower() in ("product", "products", "p", "item", "product-detail") \
                and len(slug) >= 3 and not words & self._NOT_PRODUCT:
            return True                     # /product/<anything>: the store says it's a product
        if words & self._NOT_PRODUCT:       # /about-itti-pvt-ltd, /itti-terms-and-conditions
            return False
        # "/dell-pro-plus-laptops", "/blackview-smartphones-price-nepal": a list of laptops, not one.
        meaningful = [w for w in re.split(r"[-_]+", slug.lower()) if w not in self._FILLER]
        if meaningful and meaningful[-1] in self._PLURALS:
            return False
        looks_like_model = bool(re.search(r"\d", slug)) or len(slug.split("-")) >= 4
        return (len(slug) >= 10 and looks_like_model
                and not (self.cfg.url_exclude and re.search(self.cfg.url_exclude, url, re.I)))

    def _embedded_links(self, page, host: str) -> list[str]:
        """Product links inside the page's embedded JSON (stores that draw product cards with JavaScript
        still ship the product list in __NEXT_DATA__ or similar)."""
        base = f"{urlparse(page.url).scheme or 'https'}://{host}"
        direct, slugs = [], []

        def walk(node, depth=0):
            if depth > 12:
                return
            if isinstance(node, dict):
                for k, v in node.items():
                    if isinstance(v, str) and v and len(v) < 300:
                        key = k.lower()
                        if key in ("url", "href", "link", "path", "permalink", "canonical", "producturl", "product_url"):
                            full = urljoin(base + "/", v)
                            if urlparse(full).netloc == host:
                                direct.append(full.split("#")[0])
                        elif key in ("slug", "handle", "url_key", "urlkey") and "/" not in v.strip("/"):
                            slugs.append(f"{base}/{v.strip('/')}")
                    elif isinstance(v, (dict, list)):
                        walk(v, depth + 1)
            elif isinstance(node, list):
                for v in node[:500]:
                    walk(v, depth + 1)

        for blob in _embedded_json(page):
            walk(blob)
        # Real links first; bare slugs are a guess at the URL shape (a wrong guess is just a 404).
        return list(dict.fromkeys(u for u in direct + slugs if not self._ASSET.search(u)))

    def _page(self, fetcher: Fetcher, url: str):
        """Fetch a listing page. If it has no product links (a JavaScript-built page whose product
        cards appear only after scripts run), render it in a browser."""
        host = urlparse(url).netloc
        if self.listing_mode == "dynamic":   # already known: this site draws its listings in the browser
            return fetcher.get(url, mode="dynamic", scroll=True)
        page = fetcher.get(url)
        links = self._links(page, host) + self._embedded_links(page, host)
        products = [u for u in links if self._looks_like_product(u)]
        log.info("[%s] %s: %d links, %d look like products%s", self.name, url, len(links), len(products),
                 f" (e.g. {products[0]})" if products else "")
        if not products:
            try:
                rendered = fetcher.get(url, mode="dynamic", scroll=True)
            except RateLimited:
                raise                    # the site limits us: stop it for this run
            except Exception as e:
                log.info("[%s] browser render failed for %s: %s", self.name, url, e)
                return page
            rlinks = self._links(rendered, host) + self._embedded_links(rendered, host)
            rproducts = [u for u in rlinks if self._looks_like_product(u)]
            log.info("[%s] %s in a browser: %d links, %d look like products%s", self.name, url, len(rlinks),
                     len(rproducts), f" (e.g. {rproducts[0]})" if rproducts else "")
            if rproducts or len(rlinks) > len(links):
                self.listing_mode = "dynamic"    # product pages are tried plain first (much faster)
                return rendered
        return page

    def _browse(self, fetcher: Fetcher, base: str, max_pages: int = 12) -> Iterator[str]:
        host = urlparse(base).netloc
        starts = list(self.cfg.start_urls) or [base]
        queue, fetched, found, visited = list(starts), 0, set(), set()
        categories_added = False
        while queue and fetched < max_pages:
            url = queue.pop(0)
            try:
                page = self._page(fetcher, url)
            except RateLimited:
                raise                    # the site limits us: stop it for this run
            except Exception as e:
                log.info("[%s] %s: %s", self.name, url, e)
                continue
            fetched += 1
            visited.add(url)
            links = self._links(page, host) + self._embedded_links(page, host)
            if not self.cfg.start_urls and not categories_added:
                # From the homepage, visit category pages (phones, laptops...) first.
                queue += [u for u in links if self._CATEGORY.search(urlparse(u).path)][:8]
                categories_added = True
            for u in dict.fromkeys(links):
                if u not in found and u not in visited and u not in queue and self._looks_like_product(u):
                    found.add(u)
                    yield u

    def _variant_offers(self, ld: dict, name: str, url: str, currency: str | None) -> list[Offer]:
        """schema.org ProductGroup: each hasVariant is a Product with its own price (8/128, 8/256 ...)."""
        if not _is_type(ld, "ProductGroup"):
            return []
        variants = ld.get("hasVariant") or []
        if isinstance(variants, dict):
            variants = [variants]
        out, now = [], datetime.now(timezone.utc).isoformat(timespec="seconds")
        for v in variants:
            if not isinstance(v, dict):
                continue
            vo = v.get("offers") or {}
            if isinstance(vo, list):
                vo = vo[0] if vo else {}
            price = parse_price(vo.get("price") or vo.get("lowPrice")) if isinstance(vo, dict) else None
            if price is None:
                continue
            vname = str(v.get("name") or name)
            availability = str(vo.get("availability", ""))
            out.append(Offer(
                source=self.name, url=url, price=price, currency=vo.get("priceCurrency") or currency,
                in_stock=None if not availability else "InStock" in availability, scraped_at=now,
                region=self.cfg.region, seller=self.name, official=self.cfg.official,
                variant=parse_variant(vname) or (vname if vname != name else None),
            ))
        return out

    def parse(self, page) -> Product | None:
        ld = extract_jsonld_product(page) or {}
        review = extract_jsonld_review(page)
        if not ld and review and isinstance(review.get("itemReviewed"), dict):
            ld = review["itemReviewed"]
        h1 = text_of(page.css("h1").first)
        og_title = page.css("meta[property='og:title']::attr(content)").get() or ""
        embedded = None if ld.get("offers") else extract_embedded_product(page, h1 or og_title)
        name = ld.get("name") or (
            text_of(page.css(self.cfg.name_css).first) if self.cfg.name_css else ""
        ) or h1 or (embedded or {}).get("name") or og_title
        if not name:
            return None

        brand = ld.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        elif isinstance(brand, list):
            brand = brand[0].get("name") if brand and isinstance(brand[0], dict) else None

        offers_ld = ld.get("offers") or {}
        if isinstance(offers_ld, list):
            offers_ld = offers_ld[0] if offers_ld else {}
        if isinstance(offers_ld, dict) and isinstance(offers_ld.get("offers"), list) and offers_ld["offers"]:
            offers_ld = {**offers_ld["offers"][0], **{k: v for k, v in offers_ld.items() if k != "offers"}}
        price = parse_price(offers_ld.get("price") or offers_ld.get("lowPrice"))
        currency = offers_ld.get("priceCurrency") or self.cfg.currency
        if price is None and self.cfg.price_css:
            price = parse_price(text_of(page.css(self.cfg.price_css).first))
        if price is None:
            price, meta_currency = extract_meta_price(page)
            currency = currency or meta_currency
        embedded_original = None
        if price is None and embedded:
            price, embedded_original = embedded["price"], embedded["original"]
            brand_hint = embedded.get("brand")
        else:
            brand_hint = None
        body_text = page.css("body").first.get_all_text(separator="\n") if page.css("body") else ""
        # Stores in Nepal: as a last resort, the "Rs. 54,999" shown next to "price" on the page.
        if price is None and (self.cfg.price_from_text or self.cfg.region == "np"):
            price = price_from_text(body_text)
            currency = currency or "NPR"
        if price is not None and not currency and self.cfg.region.startswith("np"):
            currency = "NPR"
        valid_until = offers_ld.get("priceValidUntil")
        # A sale price with the regular price published alongside (AggregateOffer or priceSpecification).
        original = None
        for spec in offers_ld.get("priceSpecification") or []:
            if isinstance(spec, dict) and "ListPrice" in str(spec.get("priceType", "")):
                original = parse_price(spec.get("price"))
        availability = str(offers_ld.get("availability", ""))
        in_stock = None if not availability else "InStock" in availability

        agg = ld.get("aggregateRating") or {}
        rating = review_count = None
        if agg.get("ratingValue") is not None:
            best = float(agg.get("bestRating") or 5)
            rating = round(float(agg["ratingValue"]) / best * 5, 2)
            review_count = int(agg.get("reviewCount") or agg.get("ratingCount") or 0) or None

        raw = extract_spec_rows(page, self.cfg)
        if embedded:
            for k, v in embedded["specs"].items():
                raw.setdefault(k, v)
        if not brand and brand_hint:
            brand = brand_hint
        for prop in ld.get("additionalProperty") or []:
            if isinstance(prop, dict) and prop.get("name") and prop.get("value") is not None:
                raw.setdefault(str(prop["name"]), str(prop["value"]))
        for k, v in parse_label_lines(body_text).items():
            raw.setdefault(k, v)

        specs = {}
        if review:
            rr = review["reviewRating"]
            try:
                best = float(rr.get("bestRating") or 100)
                specs["expert_score"] = round(float(rr["ratingValue"]) / best * 100, 1)
            except (KeyError, TypeError, ValueError):
                pass

        breadcrumb = " ".join(text_of(a) for a in page.css(self.cfg.breadcrumb_css))
        image = ld.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, dict):
            image = image.get("url")
        if not isinstance(image, str) or not image:
            # The image the page publishes for sharing (almost every store page has one).
            image = (page.css("meta[property='og:image']::attr(content)").get()
                     or page.css("meta[name='twitter:image']::attr(content)").get())
        if isinstance(image, str) and image:
            image = page.urljoin(image)

        offers = []
        if price is not None:
            offers.append(Offer(
                source=self.name, url=page.url, price=price, currency=currency, in_stock=in_stock,
                scraped_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                region=self.cfg.region, seller=self.name, official=self.cfg.official,
                variant=parse_variant(name), valid_until=str(valid_until)[:10] if valid_until else None,
                original_price=(original or embedded_original)
                if (original or embedded_original) and price and (original or embedded_original) > price else None,
            ))
        variant_offers = self._variant_offers(ld, name, page.url, currency)
        if variant_offers:
            offers = variant_offers      # one price per storage/colour option (ProductGroup)
        if not ld and not offers and not specs and len(raw) < 3:
            return None   # a category, article or landing page, not a product
        if not ld and not offers and self.cfg.region == "np":
            return None   # a shop page with neither product data nor a price: a category or info page
        product = Product(
            source=self.name,
            url=page.url,
            name=name.strip(),
            brand=brand,
            raw_specs=raw,
            specs=specs,
            offers=offers,
            rating=rating,
            review_count=review_count,
            image=image if isinstance(image, str) else None,
            gtin=next((str(ld[k]) for k in ("gtin13", "gtin", "gtin12", "gtin14", "gtin8") if ld.get(k)), None),
        )
        hint = self._hints.get(self._canonical(page.url, True), "")
        return finalize(product, category_hint=f"{self.cfg.category_hint or ''} {breadcrumb} {hint}")


def load_site_configs(path: str) -> dict[str, SiteConfig]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    fields = SiteConfig.__dataclass_fields__
    return {c["name"]: SiteConfig(**{k: v for k, v in c.items() if k in fields}) for c in data["sites"]}
