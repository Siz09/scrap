"""Platform adapters: one adapter covers every store built on the same e-commerce engine.

Many Nepali shops run on Shopify or WooCommerce. Both expose public product JSON
(`/products.json` and the WooCommerce Store API), which is far more stable than
CSS selectors and needs no per-site code. `detect.py` works out which one a site uses.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import datetime, timezone

from scrapling.parser import Selector

from ..models import Offer, Product
from ..normalize import categorize, clean_title, finalize, parse_label_lines, parse_price, parse_variant
from .backends import RateLimited
from .base import Fetcher, Source

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def specs_from_html(html: str | None) -> dict[str, str]:
    """Spec rows from a product description: tables, <dl>, and 'Label: value' lines."""
    if not html:
        return {}
    from .generic import SiteConfig, extract_spec_rows
    doc = Selector(f"<html><body>{html}</body></html>")
    raw = extract_spec_rows(doc, SiteConfig(name="_"))
    text = doc.css("body").first.get_all_text(separator="\n") if doc.css("body") else ""
    for k, v in parse_label_lines(text).items():
        raw.setdefault(k, v)
    return raw


def valid_gtin(code) -> str | None:
    s = re.sub(r"\D", "", str(code or ""))
    return s if len(s) in (8, 12, 13, 14) else None


class ShopifySource(Source):
    """Any Shopify store. Reads /collections/<handle>/products.json (or /products.json)."""

    def __init__(self, name: str, base_url: str, collections: list[str] | None = None,
                 currency: str = "NPR", region: str = "np", official: bool | None = None,
                 max_pages: int = 200, category_hint: str | None = None, **_):
        self.name = name
        self.base = base_url.rstrip("/")
        self.collections = collections or []
        self.currency, self.region, self.official = currency, region, official
        self.max_pages = max_pages
        self.category_hint = category_hint

    SALE_HANDLE = re.compile(r"sale|deal|offer|discount|clearance|flash|dashain|tihar|festive|festival|"
                             r"black-friday|new-year|combo", re.I)

    def sale_collections(self, fetcher: Fetcher) -> list[str]:
        """Handles of the store's sale/festival collections, from /collections.json."""
        try:
            data = fetcher.get_json(f"{self.base}/collections.json?limit=250")
        except RateLimited:
            raise                    # the site limits us: stop it for this run
        except Exception as e:
            log.info("[%s] no collections list: %s", self.name, e)
            return []
        return [c["handle"] for c in data.get("collections", [])
                if isinstance(c, dict) and self.SALE_HANDLE.search(c.get("handle", "") + " " + c.get("title", ""))]

    PAGE = 250

    def _targets(self, extra: list[str] = ()) -> list[tuple[str, str]]:
        """The whole store first (/products.json), then named collections (their handle helps
        categorise), then sale / festival collections."""
        handles = list(self.collections) + [h for h in extra if h not in self.collections]
        return [(f"{self.base}/products.json", "")] + [
            (f"{self.base}/collections/{h}/products.json", h) for h in handles]

    def parse_product(self, prod: dict, collection: str = "") -> Product | None:
        title = (prod.get("title") or "").strip()
        if not title:
            return None
        url = f"{self.base}/products/{prod.get('handle')}"
        offers, gtin = [], None
        for v in prod.get("variants") or []:
            vt = v.get("title") or ""
            price = parse_price(v.get("price"))
            original = parse_price(v.get("compare_at_price"))
            gtin = gtin or valid_gtin(v.get("barcode"))
            offers.append(Offer(
                source=self.name, url=url, price=price, currency=self.currency,
                in_stock=v.get("available"), scraped_at=_now(), region=self.region,
                seller=self.name, official=self.official,
                variant=None if vt == "Default Title" else (parse_variant(vt) or vt[:60]),
                original_price=original if original and price and original > price else None,
            ))
        tags = prod.get("tags") or []
        tags = tags if isinstance(tags, list) else str(tags).split(",")
        hint = " ".join([prod.get("product_type") or "", collection.replace("-", " "), *tags,
                         self.category_hint or ""])
        images = prod.get("images") or []
        product = Product(
            source=self.name, url=url, name=clean_title(title), brand=prod.get("vendor") or None,
            raw_specs=specs_from_html(prod.get("body_html")), offers=offers,
            image=images[0].get("src") if images else None, gtin=gtin,
        )
        product.category = categorize(product.name, hint)
        return finalize(product, hint)

    def crawl(self, fetcher: Fetcher, limit: int = 500, **_) -> Iterator[Product]:
        n = 0
        seen: set = set()        # product ids already read this run (collections overlap)
        sales = self.sale_collections(fetcher)
        if sales:
            log.info("[%s] sale collections: %s", self.name, ", ".join(sales))
        for base_url, handle in self._targets(sales):
            first_ids = None
            for page in range(1, self.max_pages + 1):
                url = f"{base_url}?limit={self.PAGE}&page={page}"
                try:
                    data = fetcher.get_json(url)
                except RateLimited:
                    raise                    # the site limits us: stop it for this run
                except Exception as e:
                    log.warning("[%s] %s: %s", self.name, url, e)
                    break
                products = data.get("products") or []
                ids = [p.get("id") for p in products]
                if not products or ids == first_ids:
                    break            # past the last page (or the store ignores ?page= and repeats)
                first_ids = first_ids or ids
                for prod in products:
                    pid = prod.get("id") or prod.get("handle")
                    if pid in seen:
                        continue
                    seen.add(pid)
                    p = self.parse_product(prod, handle)
                    if p:
                        n += 1
                        yield p
                        if n >= limit:
                            return
                if len(products) < self.PAGE:
                    break            # a short page is the last one


class WooCommerceSource(Source):
    """Any WooCommerce store, through the public Store API (/wp-json/wc/store/v1)."""

    def __init__(self, name: str, base_url: str, categories: list[str] | None = None,
                 region: str = "np", official: bool | None = None, max_pages: int = 200,
                 category_hint: str | None = None, **_):
        self.name = name
        self.api = base_url.rstrip("/") + "/wp-json/wc/store/v1"
        self.categories = categories or []
        self.region, self.official = region, official
        self.max_pages = max_pages
        self.category_hint = category_hint

    def _category_ids(self, fetcher: Fetcher) -> list[tuple[str, str]]:
        if not self.categories:
            return [("", "")]
        cats = fetcher.get_json(f"{self.api}/products/categories?per_page=100")
        by_slug = {c.get("slug"): str(c.get("id")) for c in cats if isinstance(c, dict)}
        out = []
        for slug in self.categories:
            if slug in by_slug:
                out.append((by_slug[slug], slug))
            else:
                log.warning("[%s] category %r not found; have %s", self.name, slug, sorted(by_slug)[:30])
        return out

    def parse_product(self, prod: dict, hint_extra: str = "") -> Product | None:
        name = (prod.get("name") or "").strip()
        url = prod.get("permalink") or ""
        if not name or not url:
            return None
        prices = prod.get("prices") or {}
        minor = int(prices.get("currency_minor_unit") or 0)

        def money(v):
            try:
                return int(v) / (10 ** minor) if v not in (None, "") else None
            except (TypeError, ValueError):
                return parse_price(v)

        price, regular = money(prices.get("price")), money(prices.get("regular_price"))
        raw: dict[str, str] = {}
        for attr in prod.get("attributes") or []:
            terms = ", ".join(t.get("name", "") for t in attr.get("terms") or [])
            if attr.get("name") and terms:
                raw[attr["name"]] = terms
        for k, v in specs_from_html((prod.get("description") or "") + (prod.get("short_description") or "")).items():
            raw.setdefault(k, v)
        cats = " ".join(c.get("name", "") for c in prod.get("categories") or [])
        hint = f"{cats} {hint_extra} {self.category_hint or ''}"
        rating = None
        try:
            rating = float(prod.get("average_rating") or 0) or None
        except ValueError:
            pass
        images = prod.get("images") or []
        product = Product(
            source=self.name, url=url, name=clean_title(name), raw_specs=raw,
            offers=[Offer(
                source=self.name, url=url, price=price, currency=prices.get("currency_code") or "NPR",
                in_stock=prod.get("is_in_stock"), scraped_at=_now(), region=self.region,
                seller=self.name, official=self.official, variant=parse_variant(name),
                original_price=regular if regular and price and regular > price else None,
            )],
            rating=rating, review_count=prod.get("review_count") or None,
            image=images[0].get("src") if images else None,
        )
        brands = prod.get("brands") or []
        if brands and isinstance(brands[0], dict):
            product.brand = brands[0].get("name")
        product.category = categorize(product.name, hint)
        return finalize(product, hint)

    def crawl(self, fetcher: Fetcher, limit: int = 500, **_) -> Iterator[Product]:
        n = 0
        for cat_id, slug in self._category_ids(fetcher):
            for page in range(1, self.max_pages + 1):
                url = f"{self.api}/products?per_page=100&page={page}" + (f"&category={cat_id}" if cat_id else "")
                try:
                    items = fetcher.get_json(url)
                except RateLimited:
                    raise                    # the site limits us: stop it for this run
                except Exception as e:
                    log.warning("[%s] %s: %s", self.name, url, e)
                    break
                if not items:
                    break
                for prod in items:
                    p = self.parse_product(prod, slug.replace("-", " "))
                    if p:
                        n += 1
                        yield p
                        if n >= limit:
                            return
                if len(items) < 100:
                    break            # a short page is the last one
