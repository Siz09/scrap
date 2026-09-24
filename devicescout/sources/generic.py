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
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..models import Offer, Product
from ..normalize import finalize, parse_label_lines, parse_price, parse_variant
from .base import Fetcher, Source, text_of

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
        self.fetch_mode = cfg.fetch_mode

    def _wanted(self, url: str) -> bool:
        if self.cfg.url_exclude and re.search(self.cfg.url_exclude, url, re.I):
            return False
        if self.cfg.url_include and not re.search(self.cfg.url_include, url, re.I):
            return False
        return not self.cfg.keywords or any(k.lower() in url.lower() for k in self.cfg.keywords)

    def discover(self, fetcher: Fetcher, **_) -> Iterator[str]:
        seen: set[str] = set()
        if not self.cfg.product_link_css:
            base = self.cfg.base_url or (self.cfg.start_urls[0] if self.cfg.start_urls else "")
            for u in sitemap_urls(fetcher, base):
                if u not in seen and self._wanted(u):
                    seen.add(u)
                    yield u
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

    def parse(self, page) -> Product | None:
        ld = extract_jsonld_product(page) or {}
        review = extract_jsonld_review(page)
        if not ld and review and isinstance(review.get("itemReviewed"), dict):
            ld = review["itemReviewed"]
        name = ld.get("name") or (
            text_of(page.css(self.cfg.name_css).first) if self.cfg.name_css else ""
        ) or text_of(page.css("h1").first)
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
        body_text = page.css("body").first.get_all_text(separator="\n") if page.css("body") else ""
        if price is None and self.cfg.price_from_text:
            price = price_from_text(body_text)
            currency = currency or "NPR"
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

        offers = []
        if price is not None:
            offers.append(Offer(
                source=self.name, url=page.url, price=price, currency=currency, in_stock=in_stock,
                scraped_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                region=self.cfg.region, seller=self.name, official=self.cfg.official,
                variant=parse_variant(name), valid_until=str(valid_until)[:10] if valid_until else None,
                original_price=original if original and price and original > price else None,
            ))
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
        return finalize(product, category_hint=f"{self.cfg.category_hint or ''} {breadcrumb}")


def load_site_configs(path: str) -> dict[str, SiteConfig]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    fields = SiteConfig.__dataclass_fields__
    return {c["name"]: SiteConfig(**{k: v for k, v in c.items() if k in fields}) for c in data["sites"]}
