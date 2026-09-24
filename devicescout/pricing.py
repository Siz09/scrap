"""Price sanity checks.

Nepali marketplaces carry clones ("i16 Pro Max" Android phones), refurbished units
sold as new, accessories listed in the phone category, and bait prices. A price far
below what every other seller asks is almost never a bargain, so those offers are
flagged and kept out of `best_price` (they are still stored and shown as warnings).
"""

from __future__ import annotations

import re
from statistics import median

from .models import Product

# Clone listings that borrow a flagship's name: "i16 Pro Max", "iPhone 15 Pro Max Android", "S24 Ultra copy".
_CLONE = re.compile(r"\bi1\d\s*pro\b|\biphone\b.*\bandroid\b|\b(copy|replica|clone|master\s*copy|first\s*copy)\b", re.I)


def looks_like_clone(name: str) -> bool:
    return bool(_CLONE.search(name))

# An offer this far below the typical local price is flagged.
LOCAL_FLOOR = 0.6
# ... or this far below the international price (Nepal prices are rarely much lower).
INTL_FLOOR = 0.5


def flag_suspicious(p: Product) -> Product:
    local = [o for o in p.offers if o.region.startswith("np") and o.price_npr]
    clone = looks_like_clone(p.name)
    for o in local:
        o.suspicious = clone
    for o in local:
        # Compare like with like: same storage variant when there are enough of them,
        # otherwise all variants with a looser floor (8/128 is legitimately cheaper than 12/512).
        same = [x.price_npr for x in local if x.variant and x.variant == o.variant]
        pool, floor = (same, LOCAL_FLOOR) if len(same) >= 3 else ([x.price_npr for x in local], LOCAL_FLOOR - 0.15)
        if len(pool) >= 3 and o.price_npr < floor * median(pool):
            o.suspicious = True
    ref = p.reference_price_npr
    if ref:
        for o in local:
            if o.price_npr < INTL_FLOOR * ref:
                o.suspicious = True
    return p


def discount_pct(original: float | None, price: float | None) -> float | None:
    if original and price and original > price:
        return round((original - price) / original * 100, 1)
    return None
