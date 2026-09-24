"""raw -> clean -> refine -> index, for every scraped record.

    ingest(store, product)
      1. raw     store.record_raw     keep the record exactly as parsed (dedup by content hash)
      2. clean   clean.clean          reject junk, drop impossible specs/prices, log why
      3. refine  store.upsert         merge with other sources: barcode/name matching, per-spec
                                      resolution by source priority and agreement, conflicts logged
      4. index   store._reindex       full-text index over names, aliases, brand, chipset

    reprocess(store) rebuilds steps 2-4 from the raw layer, so improved parsers and
    cleaning rules apply to everything already scraped, without touching the sites again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .clean import clean
from .models import Product
from .normalize import normalize_specs
from .storage import Store


@dataclass
class IngestStats:
    seen: int = 0
    unchanged: int = 0
    stored: int = 0
    rejected: int = 0
    fixes: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)

    def line(self) -> str:
        return (f"{self.seen} records: {self.stored} stored, {self.unchanged} unchanged since last run, "
                f"{self.rejected} rejected, {self.fixes} values fixed or dropped")


def ingest(store: Store, product: Product, stats: IngestStats | None = None, keep_raw: bool = True) -> str | None:
    stats = stats if stats is not None else IngestStats()
    stats.seen += 1
    if keep_raw and not store.record_raw(product):
        stats.unchanged += 1   # still refined below: offers carry fresh timestamps for price history
    cleaned, issues = clean(product)
    for i in issues:
        store.log_issue(i.source, i.url, i.kind, i.field, i.detail)
        stats.by_kind[i.kind] = stats.by_kind.get(i.kind, 0) + 1
        if i.kind != "rejected":
            stats.fixes += 1
    if cleaned is None:
        stats.rejected += 1
        store.db.commit()
        return None
    stats.stored += 1
    return store.upsert(cleaned)


def reprocess(store: Store) -> IngestStats:
    """Rebuild the catalogue from raw records with the current parsers and rules."""
    records = list(store.raw_records())
    store.reset_catalogue()
    stats = IngestStats()
    for p in records:
        # Re-run spec normalisation: fresh values win for every key it can produce;
        # keys only the source supplied (e.g. an expert score) are kept.
        p.specs = {**p.specs, **normalize_specs(p.raw_specs, p.category)}
        ingest(store, p, stats, keep_raw=False)
    return stats
