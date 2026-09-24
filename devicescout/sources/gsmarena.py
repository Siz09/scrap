"""GSMArena: the deepest free spec database for phones, tablets and smartwatches.

Spec only (no prices you can buy at). Pair it with retailer sources for offers.
Check their terms before running at scale; keep the default 2s+ delay.
"""

from __future__ import annotations

from collections.abc import Iterator

from datetime import datetime, timezone

from ..models import Category, Offer, Product
from ..normalize import categorize, finalize, parse_foreign_price
from .base import Fetcher, Source, text_of

BASE = "https://www.gsmarena.com/"

# Brand listing pages, e.g. https://www.gsmarena.com/samsung-phones-9.php
BRAND_PAGES = {
    "samsung": "samsung-phones-9.php",
    "apple": "apple-phones-48.php",
    "google": "google-phones-107.php",
    "xiaomi": "xiaomi-phones-80.php",
    "oneplus": "oneplus-phones-95.php",
    "motorola": "motorola-phones-4.php",
    "oppo": "oppo-phones-82.php",
    "vivo": "vivo-phones-98.php",
    "realme": "realme-phones-118.php",
    "honor": "honor-phones-121.php",
    "nothing": "nothing-phones-128.php",
    "huawei": "huawei-phones-58.php",
}


class GSMArenaSource(Source):
    name = "gsmarena"

    def discover(self, fetcher: Fetcher, brand: str = "samsung", pages: int = 1, **_) -> Iterator[str]:
        path = BRAND_PAGES.get(brand.lower())
        if not path:
            raise ValueError(f"unknown brand {brand!r}; known: {', '.join(BRAND_PAGES)}")
        url = BASE + path
        for _ in range(pages):
            page = fetcher.get(url)
            for href in page.css("div.makers li a::attr(href)").getall():
                yield page.urljoin(href)
            nxt = page.css("a.prevnextbutton[title='Next page']::attr(href)").get()
            if not nxt:
                break
            url = page.urljoin(nxt)

    def parse(self, page) -> Product | None:
        name = text_of(page.css("h1.specs-phone-name-title").first) or text_of(
            page.css("[data-spec='modelname']").first
        )
        if not name:
            return None

        # Spec tables: <table><tr><th>Section</th><td class="ttl">Label</td><td class="nfo">Value</td>
        # Section header only appears on the first row of each table.
        raw: dict[str, str] = {}
        for table in page.css("#specs-list table"):
            section = text_of(table.css("th").first)
            for row in table.css("tr"):
                label = text_of(row.css("td.ttl").first)
                value_node = row.css("td.nfo").first
                if value_node is None:
                    continue
                value = value_node.get_all_text(separator="\n").strip()
                key = f"{section} / {label}".strip()
                raw[key] = f"{raw[key]}\n{value}" if key in raw else value

        brand = name.split()[0]
        category = categorize(name)
        # GSMArena lists watches/tablets alongside phones; "Watch"/"Tab" in the name decides.
        if category in (Category.UNKNOWN, Category.PHONE) and not any(
            k.startswith("Main Camera") for k in raw
        ) and any("Watch" in w for w in name.split()):
            category = Category.SMARTWATCH
        elif category == Category.UNKNOWN:
            category = Category.PHONE

        # "Misc / Price": "$ 299.99 / € 279.00 / ₹ 24,999" -> an international reference price.
        offers = []
        price_text = next((v for k, v in raw.items() if k.endswith("/ Price")), "")
        found = parse_foreign_price(price_text)
        if found:
            amount, currency = found
            offers.append(Offer(source=self.name, url=page.url, price=amount, currency=currency,
                                region="intl", seller="GSMArena (market price abroad)",
                                scraped_at=datetime.now(timezone.utc).isoformat(timespec="seconds")))

        product = Product(
            source=self.name,
            url=page.url,
            name=name,
            brand=brand,
            category=category,
            raw_specs=raw,
            offers=offers,
            image=page.css(".specs-photo-main img::attr(src)").get(),
        )
        return finalize(product)
