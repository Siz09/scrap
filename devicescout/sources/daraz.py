"""Daraz Nepal (daraz.com.np): the largest marketplace in Nepal.

Uses the public JSON the catalog page itself loads (`?ajax=true`), so no HTML
selectors to break. Items are at `mods.listItems` (Daraz A/B-tests the shape, so a
few alternative paths are tried). Field names follow the open-source
MaheshPhuyal02/daraz-np-mcp client; confirm with `devicescout check daraz-np`.

Daraz listings carry price/seller/rating but poor specs, so this source is for
*offers*. Specs come from merging with GSMArena and store pages by canonical key.
Marketplace caveat: many sellers, grey imports and clones. `pricing.flag_suspicious`
filters implausibly cheap offers; `official` is set when the listing carries a
Mall/official badge.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from urllib.parse import urlencode

from ..models import Offer, Product
from ..normalize import categorize, clean_title, finalize, parse_price, parse_variant
from .base import Fetcher, Source

log = logging.getLogger(__name__)

# Category slugs on daraz.com.np relevant to this project.
CATEGORIES = {
    "phone": "mobile-phones",
    "laptop": "laptops",
    "tablet": "tablets",
    "smartwatch": "smart-watches",
}
# Accessories are spread over many categories; keyword search is more reliable.
ACCESSORY_QUERIES = ["power bank", "fast charger", "usb c cable", "earbuds"]

_ITEM_PATHS = [("mods", "listItems"), ("data", "mods", "listItems"), ("mods", "items"),
               ("listItems",), ("results",), ("data", "products")]


def extract_items(data) -> list[dict]:
    for path in _ITEM_PATHS:
        node = data
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, list) and node:
            return [i for i in node if isinstance(i, dict)]
    return []


def _abs(url: str, base: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return base + url
    return url


def item_to_product(item: dict, base: str = "https://www.daraz.com.np", source: str = "daraz-np",
                    category_hint: str | None = None) -> Product | None:
    name = (item.get("name") or item.get("title") or item.get("productName") or "").strip()
    url = _abs(item.get("itemUrl") or item.get("productUrl") or item.get("url") or "", base).split("?")[0]
    if not name or not url:
        return None
    price = parse_price(item.get("price") or item.get("priceShow") or item.get("salePrice"))
    original = parse_price(item.get("originalPrice") or item.get("originalPriceShow"))
    rating = None
    try:
        if item.get("ratingScore") not in (None, "", "0", "0.0"):
            rating = round(float(item["ratingScore"]), 2)
    except (TypeError, ValueError):
        pass
    try:
        reviews = int(str(item.get("review") or "0").replace(",", "")) or None
    except ValueError:
        reviews = None
    in_stock = item.get("inStock")
    if isinstance(in_stock, str):
        in_stock = in_stock.strip().lower() in ("true", "1", "yes")
    badges = " ".join(str((i or {}).get("text") or (i or {}).get("domClass") or (i or {}).get("type") or "")
                      for i in item.get("icons") or []).lower()
    official = True if ("mall" in badges or "official" in badges) else None
    brand = item.get("brandName")
    if brand and brand.strip().lower() in ("no brand", "generic", "oem"):
        brand = None

    product = Product(
        source=source, url=url, name=clean_title(name), brand=brand,
        offers=[Offer(
            source=source, url=url, price=price, currency="NPR", in_stock=in_stock,
            scraped_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            region="np", seller=item.get("sellerName") or None, official=official,
            variant=parse_variant(name), original_price=original if original and price and original > price else None,
        )],
        rating=rating, review_count=reviews, image=_abs(item.get("image") or "", base) or None,
    )
    product.category = categorize(product.name, category_hint)
    return finalize(product, category_hint)


class DarazSource(Source):
    name = "daraz-np"

    def __init__(self, name: str = "daraz-np", domain: str = "www.daraz.com.np",
                 categories: list[str] | None = None, queries: list[str] | None = None,
                 pages: int = 3, min_price: int | None = None, max_price: int | None = None, **_):
        self.name = name
        self.base = f"https://{domain}"
        self.categories = categories if categories is not None else list(CATEGORIES.values())
        self.queries = queries if queries is not None else ACCESSORY_QUERIES
        self.pages = pages
        self.min_price, self.max_price = min_price, max_price

    def listing_urls(self) -> Iterator[tuple[str, str]]:
        price = None
        if self.min_price is not None or self.max_price is not None:
            price = f"{self.min_price or 0}-{self.max_price or 99999999}"
        for slug in self.categories:
            for page in range(1, self.pages + 1):
                params = {"ajax": "true", "page": page, **({"price": price} if price else {})}
                yield f"{self.base}/{slug}/?{urlencode(params)}", slug.replace("-", " ")
        for q in self.queries:
            for page in range(1, self.pages + 1):
                params = {"ajax": "true", "q": q, "page": page, "_keyori": "ss", **({"price": price} if price else {})}
                yield f"{self.base}/catalog/?{urlencode(params)}", q

    def crawl(self, fetcher: Fetcher, limit: int = 200, **_) -> Iterator[Product]:
        try:
            fetcher.get(self.base + "/")  # pick up anti-bot cookies
        except Exception as e:
            log.warning("[%s] warm-up failed: %s", self.name, e)
        seen: set[str] = set()
        for url, hint in self.listing_urls():
            try:
                items = extract_items(fetcher.get_json(url, headers={"Referer": self.base + "/"}))
            except Exception as e:
                log.warning("[%s] %s: %s", self.name, url, e)
                continue
            if not items:
                continue
            for item in items:
                p = item_to_product(item, self.base, self.name, hint)
                if not p or p.url in seen:
                    continue
                seen.add(p.url)
                yield p
                if len(seen) >= limit:
                    return
