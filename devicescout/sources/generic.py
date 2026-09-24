"""Retailer-agnostic adapter.

Most stores embed schema.org Product JSON-LD for Google Shopping. That gives name,
brand, price, currency, stock and rating on almost any shop with zero per-site code.
Spec tables are then harvested heuristically (<table> th/td rows and <dl> dt/dd pairs).

Per-site behaviour (where to find product links, extra spec selectors, fetch mode)
comes from a JSON config, so adding a store is usually a config edit, not code.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..models import Offer, Product
from ..normalize import finalize
from .base import Fetcher, Source, text_of


@dataclass
class SiteConfig:
    name: str
    start_urls: list[str] = field(default_factory=list)
    product_link_css: str = "a[href*='/product']::attr(href)"
    next_page_css: str | None = None
    max_pages: int = 1
    fetch_mode: str = "static"
    category_hint: str | None = None
    # Optional overrides when JSON-LD is missing or wrong on this site.
    name_css: str | None = None
    price_css: str | None = None
    currency: str | None = None
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


def _is_type(obj: dict, name: str) -> bool:
    t = obj.get("@type")
    return name in t if isinstance(t, list) else t == name


def _price(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^\d.,]", "", str(value))
    if not s:
        return None
    # "1.299,00" (EU) vs "1,299.00" (US)
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if len(s.split(",")[-1]) == 2 else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def extract_jsonld_product(page) -> dict | None:
    for script in page.css("script[type='application/ld+json']::text").getall():
        try:
            data = json.loads(script)
        except (json.JSONDecodeError, TypeError):
            continue
        for obj in _walk_jsonld(data):
            if _is_type(obj, "Product") or _is_type(obj, "ProductGroup"):
                return obj
    return None


def extract_spec_rows(page, cfg: SiteConfig) -> dict[str, str]:
    raw: dict[str, str] = {}
    rows = page.css(cfg.spec_row_css) if cfg.spec_row_css else page.css("table tr")
    for row in rows:
        label = text_of(row.css(cfg.spec_label_css).first)
        value = text_of(row.css(cfg.spec_value_css).first)
        if label and value and len(label) < 60:
            raw.setdefault(label.rstrip(":"), value)
    if not cfg.spec_row_css:
        for dl in page.css("dl"):
            for dt, dd in zip(dl.css("dt"), dl.css("dd")):
                label, value = text_of(dt), text_of(dd)
                if label and value and len(label) < 60:
                    raw.setdefault(label.rstrip(":"), value)
    return raw


class GenericSource(Source):
    def __init__(self, cfg: SiteConfig):
        self.cfg = cfg
        self.name = cfg.name
        self.fetch_mode = cfg.fetch_mode

    def discover(self, fetcher: Fetcher, **_) -> Iterator[str]:
        seen: set[str] = set()
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
        price = _price(offers_ld.get("price") or offers_ld.get("lowPrice"))
        currency = offers_ld.get("priceCurrency") or self.cfg.currency
        if price is None and self.cfg.price_css:
            price = _price(text_of(page.css(self.cfg.price_css).first))
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

        breadcrumb = " ".join(text_of(a) for a in page.css(self.cfg.breadcrumb_css))
        image = ld.get("image")
        if isinstance(image, list):
            image = image[0] if image else None

        product = Product(
            source=self.name,
            url=page.url,
            name=name.strip(),
            brand=brand,
            raw_specs=raw,
            offers=[Offer(
                source=self.name, url=page.url, price=price, currency=currency, in_stock=in_stock,
                scraped_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )],
            rating=rating,
            review_count=review_count,
            image=image if isinstance(image, str) else None,
        )
        return finalize(product, category_hint=f"{self.cfg.category_hint or ''} {breadcrumb}")


def load_site_configs(path: str) -> dict[str, SiteConfig]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {c["name"]: SiteConfig(**c) for c in data["sites"]}
