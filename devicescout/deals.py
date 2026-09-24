"""Deals, checked against evidence the store doesn't control.

A store's crossed-out price is a claim. Nepali retail, festival sales especially,
often inflates the "MRP" just before a discount. So every discounted or unusually
cheap offer is compared with:

  - the market price: the median of other trustworthy sellers' current prices for the
    same model, or failing that, the median of the prices we've recorded recently;
  - the lowest price we've ever recorded for the model.

Verdicts:
  real deal          at least 12% below the market price
  good price         7-12% below the market price
  price drop         this seller cut its own price by 5% or more since we last saw it
  lowest price seen  at or below the lowest price we've recorded (needs history)

Being the cheapest of several sellers by a few percent is normal price spread, not a
deal: an offer only counts with a claimed discount, a real price drop, or a saving of
at least 12% against the market.
  paper discount     the store claims a discount but the price is no better than the market
  inflated original  the crossed-out price is well above anything the model sold for
  unverified         a claimed discount with nothing to check it against
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median

from .models import Category, Offer, Product
from .storage import Store

REAL = 0.12
GOOD = 0.07
DROP = 0.05
INFLATED = 1.15        # crossed-out price this far above the highest price seen = inflated
HISTORY_DAYS = 60


@dataclass
class Deal:
    product: Product
    offer: Offer
    claimed_pct: float | None       # what the store says (from its crossed-out price)
    market_price: float | None      # what the model typically sells for
    saving: float | None            # market_price - price, in NPR
    saving_pct: float | None
    lowest_seen: float | None
    verdicts: list[str] = field(default_factory=list)
    dropped_from: float | None = None   # this seller's previous price, when it cut it

    @property
    def verified(self) -> bool:
        return any(v in ("real deal", "good price", "price drop") for v in self.verdicts)

    def to_dict(self) -> dict:
        o, p = self.offer, self.product
        return {
            "key": p.key, "name": p.name, "brand": p.brand, "category": p.category.value, "image": p.image,
            "rating": p.rating, "specs": p.specs,
            "seller": o.seller or o.source, "url": o.url, "variant": o.variant, "official": o.official,
            "in_stock": o.in_stock, "price": o.price_npr, "original_price": o.original_price,
            "valid_until": o.valid_until, "claimed_pct": self.claimed_pct, "market_price": self.market_price,
            "saving": self.saving, "saving_pct": self.saving_pct, "lowest_seen": self.lowest_seen,
            "verdicts": self.verdicts, "verified": self.verified, "dropped_from": self.dropped_from,
        }


def _pct(a: float, b: float) -> float:
    return round((a - b) / a * 100, 1)


def evaluate(p: Product, history: list[tuple[str, float, str | None]]) -> list[Deal]:
    """Deals among a product's current offers. history: [(scraped_at, price_npr, url)] for trusted local offers."""
    local = p.local_offers()
    if not local:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()
    recent = [price for at, price, _ in history if at >= cutoff]
    all_prices = [price for _, price, _ in history]
    lowest = min(all_prices) if len(all_prices) >= 3 else None
    highest = max(all_prices + [o.price_npr for o in local])

    deals = []
    for o in local:
        price = o.price_npr
        # Compare like with like: same storage variant when other sellers list it.
        peers = [x.price_npr for x in local if x is not o and (x.variant == o.variant or not o.variant)]
        if len(peers) >= 2:
            market = median(peers)
        elif len(recent) >= 3:
            market = median(recent)
        elif peers:
            market = peers[0]
        else:
            market = None
        saving = round(market - price) if market else None
        saving_pct = _pct(market, price) / 100 if market else None
        claimed = _pct(o.original_price, price) if o.original_price and o.original_price > price else None
        before = [pr for at, pr, url in history if url == o.url and at < (o.scraped_at or "")]
        dropped = (before[-1] - price) / before[-1] if before and before[-1] > 0 else 0

        verdicts = []
        if saving_pct is not None and saving_pct >= REAL:
            verdicts.append("real deal")
        elif saving_pct is not None and saving_pct >= GOOD:
            verdicts.append("good price")
        if dropped >= DROP:
            verdicts.append("price drop")
        if lowest is not None and price <= lowest:
            verdicts.append("lowest price seen")
        if claimed:
            evidence = market is not None or bool(all_prices)   # something other than the offer itself
            if evidence and o.original_price > INFLATED * max(highest, market or 0):
                verdicts.append("inflated original")
            if saving_pct is not None and saving_pct < GOOD:
                verdicts.append("paper discount")
            if market is None and lowest is None:
                verdicts.append("unverified")
        is_deal = bool(claimed and claimed >= 5) or "real deal" in verdicts or "price drop" in verdicts
        if is_deal:
            deals.append(Deal(p, o, claimed, market, saving,
                              round(saving_pct * 100, 1) if saving_pct is not None else None, lowest, verdicts,
                              before[-1] if dropped >= DROP else None))
    return deals


def find_deals(store: Store, category: Category | None = None, verified_only: bool = False) -> list[Deal]:
    out: list[Deal] = []
    for p in store.products(category):
        flagged = {(o.url, o.variant or "") for o in p.offers if o.suspicious}
        history = [(r["scraped_at"], r["price"], r["url"]) for r in store.price_history(p.key)
                   if r["region"].startswith("np") and (r["currency"] or "NPR") == "NPR" and r["price"]
                   and (r["url"], r["variant"]) not in flagged]
        out.extend(d for d in evaluate(p, history) if d.verified or not verified_only)
    # Verified savings first, biggest real saving first; paper-only discounts last.
    out.sort(key=lambda d: (not d.verified, "inflated original" in d.verdicts,
                            -(d.saving_pct or 0), -(d.claimed_pct or 0)))
    return out
