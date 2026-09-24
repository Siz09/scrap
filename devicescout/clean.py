"""Cleaning: reject junk and impossible values before they reach the catalogue.

Scraped data is wrong in predictable ways: a power bank's 20000 mAh read as a phone
battery, a price field holding an EMI instalment, "1 kg" for a phone, HTML entities in
names. Each rule here fixes or drops the bad value and records why, so the quality log
shows what the scrapers are getting wrong instead of it silently skewing rankings.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date

from .models import Category, Product

C = Category


@dataclass
class Issue:
    source: str
    url: str
    kind: str        # "rejected" | "dropped_spec" | "dropped_offer" | "fixed"
    field: str
    detail: str


# Plausible ranges per spec, per category (None = any category).
_ANY = None
RANGES: dict[str, dict[Category | None, tuple[float, float]]] = {
    "battery_mah": {C.PHONE: (1000, 12000), C.TABLET: (3000, 15000), C.SMARTWATCH: (100, 1500),
                    C.EARBUDS: (20, 1000), _ANY: (20, 30000)},
    "capacity_mah": {_ANY: (1000, 60000)},
    "battery_wh": {_ANY: (10, 110)},
    "ram_gb": {C.PHONE: (1, 24), C.TABLET: (1, 32), _ANY: (1, 256)},
    "storage_gb": {_ANY: (1, 16384)},
    "display_size_in": {C.PHONE: (3.5, 8.5), C.TABLET: (7, 15), C.LAPTOP: (10, 19), C.SMARTWATCH: (0.8, 2.5),
                        _ANY: (0.5, 20)},
    "weight_g": {C.PHONE: (90, 350), C.TABLET: (250, 1000), C.LAPTOP: (700, 5000), C.SMARTWATCH: (10, 150),
                 C.POWER_BANK: (50, 1500), _ANY: (1, 6000)},
    "refresh_rate_hz": {_ANY: (30, 540)},
    "charging_w": {_ANY: (2, 240)},
    "output_w": {_ANY: (2, 300)},
    "main_camera_mp": {_ANY: (1, 300)},
    "optical_zoom_x": {_ANY: (1, 100)},
    "camera_count": {_ANY: (1, 6)},
    "release_year": {_ANY: (2010, date.today().year + 1)},
    "os_upgrades": {_ANY: (0, 10)},
    "expert_score": {_ANY: (0, 100)},
}

# Prices outside these bounds (NPR) are almost always parse errors: EMI instalments,
# "Rs 1" placeholders, or several prices glued together.
PRICE_RANGE: dict[Category | None, tuple[float, float]] = {
    C.PHONE: (3000, 600000), C.LAPTOP: (15000, 1500000), C.TABLET: (5000, 600000),
    C.SMARTWATCH: (800, 300000), C.POWER_BANK: (300, 50000), C.CHARGER: (100, 30000),
    C.EARBUDS: (200, 100000), C.CABLE: (50, 15000), C.CASE: (50, 20000), _ANY: (50, 3000000),
}

_JUNK_NAME = re.compile(r"^(test|sample|dummy|n/?a|null|none|product|item)\b|^\W*$", re.I)


def _range(table: dict, cat: Category) -> tuple[float, float] | None:
    return table.get(cat) or table.get(_ANY)


def clean(p: Product) -> tuple[Product | None, list[Issue]]:
    issues: list[Issue] = []

    def note(kind, field, detail):
        issues.append(Issue(p.source, p.url, kind, field, detail))

    # Names: entities, stray whitespace, junk.
    name = re.sub(r"\s+", " ", html.unescape(p.name or "")).strip()
    if name != p.name:
        note("fixed", "name", "unescaped/collapsed whitespace")
        p.name = name
    if len(name) < 3 or _JUNK_NAME.search(name):
        note("rejected", "name", f"not a product name: {name!r}")
        return None, issues
    if p.brand:
        p.brand = html.unescape(p.brand).strip() or None

    # Specs outside what the device type physically allows.
    for key, bounds in RANGES.items():
        v = p.specs.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        lo, hi = _range(bounds, p.category)
        if not lo <= v <= hi:
            note("dropped_spec", key, f"{v:g} outside {lo:g}-{hi:g} for {p.category.value}")
            del p.specs[key]

    # Offers: impossible prices, discounts that aren't.
    lo, hi = _range(PRICE_RANGE, p.category)
    kept = []
    for o in p.offers:
        v = o.price_npr
        if o.price is not None and (v is None or not lo <= v <= hi):
            note("dropped_offer", "price", f"{o.seller or o.source}: {o.price:g} {o.currency or ''} outside "
                                           f"Rs {lo:,.0f}-{hi:,.0f} for {p.category.value}")
            continue
        if o.original_price and o.price and (o.original_price <= o.price or o.original_price > o.price * 3):
            o.original_price = None
            note("fixed", "original_price", "discount price not plausible; ignored")
        kept.append(o)
    p.offers = kept

    if p.rating is not None and not 0 <= p.rating <= 5:
        note("fixed", "rating", f"{p.rating} outside 0-5; ignored")
        p.rating = None
    return p, issues
