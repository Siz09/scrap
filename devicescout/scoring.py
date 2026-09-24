"""Preference-based ranking.

Scores are *relative*: each spec is converted to a percentile among the candidates
being compared (same category, after filters). A phone with 12 GB RAM is "good"
only relative to what else is on the list. That avoids hand-tuning absolute
thresholds that go stale every product cycle.

Every result carries a `coverage` figure: the share of the profile's weight backed
by real data. A 90-point score at 30% coverage is a guess; show that to users.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import Category, Product

# (spec_key, weight, higher_is_better)
Weights = list[tuple[str, float, bool]]

PROFILES: dict[str, dict[Category, Weights]] = {
    "photography": {
        Category.PHONE: [("main_camera_mp", 0.15, True), ("optical_zoom_x", 0.30, True),
                         ("has_ois", 0.20, True), ("camera_count", 0.15, True),
                         ("storage_gb", 0.10, True), ("chip_tier", 0.10, True)],
        Category.TABLET: [("main_camera_mp", 0.5, True), ("display_size_in", 0.5, True)],
    },
    "gaming": {
        Category.PHONE: [("chip_tier", 0.35, True), ("benchmark_score", 0.15, True),
                         ("refresh_rate_hz", 0.20, True), ("ram_gb", 0.10, True),
                         ("battery_mah", 0.10, True), ("charging_w", 0.10, True)],
        Category.LAPTOP: [("has_dedicated_gpu", 0.35, True), ("benchmark_score", 0.15, True),
                          ("refresh_rate_hz", 0.20, True), ("ram_gb", 0.15, True),
                          ("storage_gb", 0.10, True), ("chip_tier", 0.05, True)],
        Category.TABLET: [("chip_tier", 0.5, True), ("refresh_rate_hz", 0.3, True), ("ram_gb", 0.2, True)],
    },
    "battery": {
        Category.PHONE: [("battery_mah", 0.6, True), ("charging_w", 0.3, True), ("weight_g", 0.1, False)],
        Category.SMARTWATCH: [("battery_mah", 0.8, True), ("weight_g", 0.2, False)],
        Category.LAPTOP: [("battery_wh", 0.8, True), ("weight_g", 0.2, False)],
        Category.POWER_BANK: [("capacity_mah", 0.5, True), ("output_w", 0.35, True), ("weight_g", 0.15, False)],
    },
    "portability": {
        Category.PHONE: [("weight_g", 0.5, False), ("display_size_in", 0.3, False), ("battery_mah", 0.2, True)],
        Category.LAPTOP: [("weight_g", 0.6, False), ("battery_wh", 0.3, True), ("display_size_in", 0.1, False)],
        Category.POWER_BANK: [("weight_g", 0.6, False), ("capacity_mah", 0.2, True), ("output_w", 0.2, True)],
        Category.SMARTWATCH: [("weight_g", 0.6, False), ("battery_mah", 0.4, True)],
    },
    "display": {
        Category.PHONE: [("resolution_px", 0.35, True), ("refresh_rate_hz", 0.35, True), ("display_size_in", 0.3, True)],
        Category.LAPTOP: [("resolution_px", 0.4, True), ("refresh_rate_hz", 0.3, True), ("display_size_in", 0.3, True)],
        Category.TABLET: [("resolution_px", 0.4, True), ("refresh_rate_hz", 0.3, True), ("display_size_in", 0.3, True)],
    },
    "balanced": {
        Category.PHONE: [("chip_tier", 0.2, True), ("main_camera_mp", 0.1, True), ("optical_zoom_x", 0.1, True),
                         ("battery_mah", 0.15, True), ("charging_w", 0.1, True), ("refresh_rate_hz", 0.1, True),
                         ("storage_gb", 0.1, True), ("rating", 0.15, True)],
        Category.LAPTOP: [("chip_tier", 0.25, True), ("ram_gb", 0.2, True), ("storage_gb", 0.15, True),
                          ("battery_wh", 0.15, True), ("weight_g", 0.1, False), ("rating", 0.15, True)],
        Category.SMARTWATCH: [("battery_mah", 0.4, True), ("weight_g", 0.2, False), ("rating", 0.4, True)],
        Category.POWER_BANK: [("capacity_mah", 0.35, True), ("output_w", 0.35, True), ("rating", 0.3, True)],
        Category.TABLET: [("chip_tier", 0.3, True), ("display_size_in", 0.2, True), ("storage_gb", 0.2, True),
                          ("rating", 0.3, True)],
    },
}

# Rough SoC tiers (0-10). Crude on purpose; replace with real benchmark data when a
# benchmark source is added (Geekbench/3DMark/Notebookcheck) -> `benchmark_score`.
_CHIP_TIERS: list[tuple[str, float]] = [
    (r"snapdragon 8 elite|a1[89] pro|a19|dimensity 9[45]00|m[45]\b|m[34] (pro|max)|tensor g5", 10),
    (r"snapdragon 8 gen 3|a1[78] pro|a18\b|dimensity 9300|core ultra 9|ryzen (ai )?9|m3\b|exynos 2[45]00", 9),
    (r"snapdragon 8s? gen [12]|a1[56]|dimensity 9[02]00|tensor g[34]|core ultra 7|i9-1[34]|ryzen 7|m2", 8),
    (r"snapdragon 7\+? ?(s )?gen [23]|dimensity 8[0-9]00|tensor g[12]|i7-1[2-4]|core ultra 5|exynos 2200|m1", 7),
    (r"snapdragon 7|dimensity 7[0-9]{3}|exynos 1[34]80|i5-1[2-4]|ryzen 5|helio g99", 5),
    (r"snapdragon [46]|dimensity [678]0|helio|exynos 850|unisoc|celeron|pentium|i3-|athlon|mediatek", 3),
]


def chip_tier(chipset: str | None) -> float | None:
    if not chipset:
        return None
    c = chipset.lower()
    for pattern, tier in _CHIP_TIERS:
        if re.search(pattern, c):
            return tier
    return None


def _value(p: Product, key: str) -> float | None:
    if key == "chip_tier":
        return chip_tier(p.specs.get("chipset"))
    if key == "rating":
        return p.rating
    v = p.specs.get(key)
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return float(v) if isinstance(v, (int, float)) else None


def _percentiles(values: list[float | None], higher_is_better: bool) -> list[float | None]:
    present = sorted(v for v in values if v is not None)
    if not present:
        return [None] * len(values)
    n = len(present)
    out: list[float | None] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        # mid-rank percentile so ties share a score and a lone item gets 0.5... but
        # a single candidate is trivially "best", so give it 1.0.
        below = sum(1 for x in present if x < v)
        equal = sum(1 for x in present if x == v)
        pct = 1.0 if n == 1 else (below + (equal - 1) / 2) / (n - 1)
        out.append(pct if higher_is_better else 1 - pct)
    return out


@dataclass
class Ranked:
    product: Product
    score: float        # 0-100
    coverage: float     # 0-1
    breakdown: dict[str, float | None]
    value_score: float | None = None  # score per 100 units of currency

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.product.name, "category": self.product.category.value,
            "score": round(self.score, 1), "coverage": round(self.coverage, 2),
            "value_score": None if self.value_score is None else round(self.value_score, 2),
            "best_price": self.product.best_price,
            "os": self.product.specs.get("os"),
            "breakdown": {k: None if v is None else round(v, 2) for k, v in self.breakdown.items()},
            "sources": sorted({o.source for o in self.product.offers} | {self.product.source}),
        }


def rank(products: list[Product], profile: str, category: Category,
         os: str | None = None, max_price: float | None = None,
         min_coverage: float = 0.0) -> list[Ranked]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; choose from {', '.join(PROFILES)}")
    weights = PROFILES[profile].get(category) or PROFILES["balanced"].get(category)
    if not weights:
        raise ValueError(f"profile {profile!r} has no weights for {category.value}")

    pool = [p for p in products if p.category == category]
    if os:
        pool = [p for p in pool if p.specs.get("os") == os.lower()]
    if max_price is not None:
        pool = [p for p in pool if p.best_price is not None and p.best_price <= max_price]
    if not pool:
        return []

    columns = {key: _percentiles([_value(p, key) for p in pool], hib) for key, _, hib in weights}
    total_w = sum(w for _, w, _ in weights)
    results: list[Ranked] = []
    for i, p in enumerate(pool):
        breakdown = {key: columns[key][i] for key, _, _ in weights}
        known_w = sum(w for key, w, _ in weights if breakdown[key] is not None)
        coverage = known_w / total_w
        if coverage == 0 or coverage < min_coverage:
            continue
        # Score over the specs we know, then shrink toward 50 by missing coverage,
        # so sparse listings can't top the chart on one lucky spec.
        known_score = sum(breakdown[k] * w for k, w, _ in weights if breakdown[k] is not None) / known_w
        score = 100 * (coverage * known_score + (1 - coverage) * 0.5)
        price = p.best_price
        results.append(Ranked(p, score, coverage, breakdown,
                              value_score=score / price * 100 if price else None))
    results.sort(key=lambda r: r.score, reverse=True)
    return results
