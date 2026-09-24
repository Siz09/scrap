"""Turn a buyer's needs + budget into a short, explained shortlist.

Pipeline:
  1. Hard filters: category, budget (NPR, local offers only), OS, brands, must-haves.
     A must-have we have no data for does not exclude a device; it is flagged
     "unverified" and costs points, so incomplete listings don't win by omission.
  2. Blend the buyer's uses (e.g. photography x2 + battery x1) into one weight set,
     plus a fixed "quality" share (expert reviews, user ratings, model year) so a device
     that looks great on paper but reviews badly can't top the list.
  3. Percentile-score the candidates against each other (scoring.score_pool).
  4. Return: top picks in budget, a value pick, a "stretch" pick slightly over budget
     when it is clearly better, and plain-language reasons for each.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import Category, Product
from .scoring import PROFILES, Ranked, Weights, spec_value, score_pool

# Fixed share of every recommendation given to "is it actually good" signals.
QUALITY_SHARE = 0.2
QUALITY_WEIGHTS: Weights = [("expert_score", 0.5, True), ("rating", 0.3, True), ("release_year", 0.2, True)]
STRETCH = 0.15            # look up to 15% over budget for a clearly better device
STRETCH_MIN_GAIN = 8.0    # ...only if it scores at least this many points higher
UNVERIFIED_PENALTY = 4.0  # points per must-have we could not confirm

# Human wording for explanations: key -> (label, formatter)
LABELS: dict[str, tuple[str, Any]] = {
    "main_camera_mp": ("main camera", lambda v: f"{v:g} MP"),
    "optical_zoom_x": ("optical zoom", lambda v: f"{v:g}x"),
    "has_ois": ("OIS stabilisation", lambda v: "yes" if v else "no"),
    "camera_count": ("rear cameras", lambda v: f"{v:g}"),
    "chip_tier": ("processor class", lambda v: f"tier {v:g}/10"),
    "benchmark_score": ("benchmark", lambda v: f"{v:,.0f}"),
    "expert_score": ("expert review score", lambda v: f"{v:g}/100"),
    "rating": ("buyer rating", lambda v: f"{v:.1f}/5"),
    "release_year": ("model year", lambda v: f"{v:g}"),
    "refresh_rate_hz": ("screen refresh rate", lambda v: f"{v:g} Hz"),
    "resolution_px": ("screen resolution", lambda v: f"{v / 1e6:.1f} MP"),
    "display_quality": ("display", lambda v: "sharp & smooth" if v > 7 else "average"),
    "display_size_in": ("screen size", lambda v: f'{v:g}"'),
    "ram_gb": ("RAM", lambda v: f"{v:g} GB"),
    "storage_gb": ("storage", lambda v: f"{v:g} GB" if v < 1024 else f"{v / 1024:g} TB"),
    "battery_mah": ("battery", lambda v: f"{v:,.0f} mAh"),
    "battery_wh": ("battery", lambda v: f"{v:g} Wh"),
    "charging_w": ("charging speed", lambda v: f"{v:g} W"),
    "weight_g": ("weight", lambda v: f"{v:,.0f} g"),
    "has_dedicated_gpu": ("dedicated graphics", lambda v: "yes" if v else "no"),
    "capacity_mah": ("capacity", lambda v: f"{v:,.0f} mAh"),
    "output_w": ("max output", lambda v: f"{v:g} W"),
    "has_gps": ("built-in GPS", lambda v: "yes" if v else "no"),
    "has_nfc": ("NFC", lambda v: "yes" if v else "no"),
    "water_rating": ("water resistance", lambda v: f"level {v:g}"),
}


@dataclass
class Needs:
    category: Category
    budget_max: float | None = None          # NPR
    budget_min: float | None = None
    uses: dict[str, float] = field(default_factory=lambda: {"balanced": 1.0})  # use -> priority
    os: list[str] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)          # only these (empty = any)
    exclude_brands: list[str] = field(default_factory=list)
    # Must-haves: spec_key -> (op, value); op in {">=", "<=", "=="}
    must: dict[str, tuple[str, Any]] = field(default_factory=dict)
    official_only: bool = False
    in_stock_only: bool = False
    top: int = 5

    @staticmethod
    def parse_budget(text: str) -> tuple[float | None, float | None]:
        """'30000-50000', '50k', '1.5 lakh', '-60000', '40000-' -> (min, max) in NPR."""
        def amount(s: str) -> float | None:
            s = s.strip().lower().replace(",", "").replace("rs", "").replace("npr", "").strip(". ")
            if not s:
                return None
            mult = 1
            if s.endswith(("lakh", "lac", "l")):
                mult, s = 100_000, s.rstrip("lakhc ").strip()
            elif s.endswith("k"):
                mult, s = 1000, s[:-1]
            return float(s) * mult
        if "-" in text:
            lo, hi = text.split("-", 1)
            return amount(lo), amount(hi)
        return None, amount(text)


@dataclass
class Pick:
    ranked: Ranked
    price: float | None
    strengths: list[str]
    weaknesses: list[str]
    warnings: list[str]
    where_to_buy: list[dict]

    def to_dict(self) -> dict:
        p = self.ranked.product
        return {
            "name": p.name, "brand": p.brand, "score": round(self.ranked.score, 1),
            "confidence": round(self.ranked.coverage, 2), "price_npr": self.price,
            "strengths": self.strengths, "weaknesses": self.weaknesses, "warnings": self.warnings,
            "where_to_buy": self.where_to_buy, "specs": p.specs,
        }


@dataclass
class Advice:
    needs: Needs
    picks: list[Pick]
    value_pick: Pick | None
    stretch_pick: Pick | None
    considered: int
    excluded: dict[str, int]

    def to_dict(self) -> dict:
        return {
            "picks": [p.to_dict() for p in self.picks],
            "value_pick": self.value_pick.to_dict() if self.value_pick else None,
            "stretch_pick": self.stretch_pick.to_dict() if self.stretch_pick else None,
            "considered": self.considered, "excluded": self.excluded,
        }


def blend_weights(category: Category, uses: dict[str, float]) -> Weights:
    combined: dict[tuple[str, bool], float] = {}
    total_priority = sum(uses.values()) or 1
    for use, priority in uses.items():
        if use not in PROFILES:
            raise ValueError(f"unknown use {use!r}; choose from {', '.join(PROFILES)}")
        weights = PROFILES[use].get(category) or PROFILES["balanced"].get(category) or []
        s = sum(w for _, w, _ in weights) or 1
        for key, w, hib in weights:
            combined[(key, hib)] = combined.get((key, hib), 0) + (1 - QUALITY_SHARE) * (priority / total_priority) * w / s
    for key, w, hib in QUALITY_WEIGHTS:
        combined[(key, hib)] = combined.get((key, hib), 0) + QUALITY_SHARE * w
    return [(k, w, hib) for (k, hib), w in combined.items()]


def _check_must(p: Product, key: str, op: str, want) -> bool | None:
    have = spec_value(p, key)
    if have is None:
        return None
    if op == ">=":
        return have >= want
    if op == "<=":
        return have <= want
    return have == want


def _explain(r: Ranked, needs: Needs, unverified: list[str], pool_size: int,
             importance: dict[str, float]) -> tuple[list[str], list[str], list[str]]:
    p = r.product
    strengths, weaknesses, warnings = [], [], []
    # What the buyer weighted most comes first, so a photography buyer reads about the camera.
    for key, pct in sorted(r.breakdown.items(), key=lambda kv: -importance.get(kv[0], 0)):
        if pct is None or key not in LABELS or pool_size < 2:
            continue
        label, fmt = LABELS[key]
        raw = spec_value(p, key)
        shown = f" ({fmt(raw)})" if raw is not None else ""
        if key == "weight_g":
            good = f"one of the lightest{shown} in this budget" if pool_size >= 4 else f"lightest{shown} of the options"
            bad = f"heavier{shown} than alternatives"
        else:
            good = (f"top-tier {label}{shown} for this budget" if pool_size >= 4
                    else f"best {label}{shown} of the options")
            bad = f"weaker {label}{shown} than alternatives"
        if pct >= 0.75:
            strengths.append(good)
        elif pct <= 0.25:
            weaknesses.append(bad)
    if unverified:
        warnings.append("could not confirm: " + ", ".join(unverified))
    if r.coverage < 0.5:
        warnings.append(f"limited spec data ({r.coverage:.0%} of what matters for you); treat the score as rough")
    flagged = [o for o in p.offers if o.suspicious]
    if flagged:
        n = len(flagged)
        warnings.append(f"{n} listing{'s' if n > 1 else ''} ignored as implausibly cheap (possible clone, used, or mislisted)")
    local = p.local_offers(needs.in_stock_only)
    if local and not any(o.official for o in local):
        warnings.append("no seller confirmed as official/authorised: check warranty before paying")
    ref, best = p.reference_price_npr, p.best_price
    if ref and best and best > ref * 1.3:
        warnings.append(f"Nepal price is {best / ref - 1:.0%} above the international reference (before import costs)")
    return strengths[:3], weaknesses[:2], warnings


def _where(p: Product, needs: Needs) -> list[dict]:
    offers = sorted(p.local_offers(needs.in_stock_only), key=lambda o: o.price_npr)
    if needs.official_only:
        offers = [o for o in offers if o.official]
    return [{"seller": o.seller or o.source, "price_npr": o.price_npr, "variant": o.variant,
             "official": o.official, "in_stock": o.in_stock, "listed_price_only": o.region == "np-ref",
             "url": o.url} for o in offers[:3]]


def _local_price(p: Product, needs: Needs) -> float | None:
    """Cheapest trustworthy Nepali price that satisfies the buyer's seller filters."""
    offers = p.local_offers(needs.in_stock_only)
    if needs.official_only:
        offers = [o for o in offers if o.official]
    return min((o.price_npr for o in offers), default=None)


def advise(products: list[Product], needs: Needs) -> Advice:
    excluded = {"wrong_category": 0, "no_nepal_price": 0, "over_budget": 0, "under_min_budget": 0,
                "os": 0, "brand": 0, "must_have": 0}
    ceiling = needs.budget_max * (1 + STRETCH) if needs.budget_max else None
    pool: list[Product] = []
    unverified: dict[int, list[str]] = {}

    for p in products:
        if p.category != needs.category:
            excluded["wrong_category"] += 1
            continue
        price = _local_price(p, needs)
        if price is None:
            excluded["no_nepal_price"] += 1
            continue
        if ceiling and price > ceiling:
            excluded["over_budget"] += 1
            continue
        if needs.budget_min and price < needs.budget_min:
            excluded["under_min_budget"] += 1
            continue
        os_unknown = needs.os and p.specs.get("os") is None
        if needs.os and not os_unknown and p.specs["os"] not in [o.lower() for o in needs.os]:
            excluded["os"] += 1
            continue
        brand = (p.brand or p.name.split()[0]).lower()
        if (needs.brands and brand not in [b.lower() for b in needs.brands]) or \
                brand in [b.lower() for b in needs.exclude_brands]:
            excluded["brand"] += 1
            continue
        failed, unknown = False, []
        for key, (op, want) in needs.must.items():
            ok = _check_must(p, key, op, want)
            if ok is False:
                failed = True
                break
            if ok is None:
                unknown.append(LABELS.get(key, (key,))[0])
        if failed:
            excluded["must_have"] += 1
            continue
        if os_unknown:
            unknown.append("operating system")
        unverified[id(p)] = unknown
        pool.append(p)

    weights = blend_weights(needs.category, needs.uses)

    def scored(products: list[Product]) -> list[Ranked]:
        out = score_pool(products, weights)
        for r in out:
            r.score -= UNVERIFIED_PENALTY * len(unverified[id(r.product)])
        return out

    def price_of(r: Ranked) -> float:
        return _local_price(r.product, needs)

    importance: dict[str, float] = {}
    for key, w, _ in weights:
        importance[key] = importance.get(key, 0) + w

    def pick(r: Ranked, pool_size: int) -> Pick:
        s, w, warn = _explain(r, needs, unverified[id(r.product)], pool_size, importance)
        return Pick(r, price_of(r), s, w, warn, _where(r.product, needs))

    # Picks are scored only against devices the buyer can afford, so "top-tier X for this
    # budget" is literally true. Stretch candidates are judged on a combined scale.
    affordable = [p for p in pool if not needs.budget_max or _local_price(p, needs) <= needs.budget_max]
    # Devices within stretch range but over budget count as "over budget" in the summary.
    excluded["over_budget"] += len(pool) - len(affordable)
    in_budget = sorted(scored(affordable), key=lambda r: r.score, reverse=True)
    top = in_budget[: needs.top]

    value_pick = None
    if in_budget:
        bar = in_budget[0].score * 0.85
        good_enough = [r for r in in_budget if r.score >= bar]
        v = min(good_enough, key=price_of)
        if v is not in_budget[0]:
            value_pick = pick(v, len(in_budget))

    stretch_pick = None
    if needs.budget_max and len(affordable) < len(pool):
        combined = scored(pool)
        over = [r for r in combined if price_of(r) > needs.budget_max]
        best_in = max((r.score for r in combined if price_of(r) <= needs.budget_max), default=None)
        best_over = max(over, key=lambda r: r.score, default=None)
        if best_over and (best_in is None or best_over.score >= best_in + STRETCH_MIN_GAIN):
            stretch_pick = pick(best_over, len(combined))

    return Advice(needs, [pick(r, len(in_budget)) for r in top], value_pick, stretch_pick, len(affordable), excluded)
