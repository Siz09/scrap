"""Build Source objects from sources.json entries.

Entry fields (all sources):
  name, type, enabled, region ("np" store | "np-ref" Nepali listed price | "intl"),
  role ("offers" | "specs" | "reviews" | "reference"), verified, notes
type: gsmarena | daraz | shopify | woocommerce | jsonld | auto
Remaining fields are passed to the adapter.
"""

from __future__ import annotations

import json
import logging

from .base import Fetcher, Source
from .daraz import DarazSource
from .detect import cached_platform, detect, remember
from .generic import GenericSource, SiteConfig
from .gsmarena import GSMArenaSource
from .platforms import ShopifySource, WooCommerceSource

log = logging.getLogger(__name__)

_META = {"type", "enabled", "role", "verified", "notes", "brands"}


def load_entries(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["sources"]


def build(entry: dict, fetcher: Fetcher | None = None) -> Source:
    kind = entry.get("type", "auto")
    opts = {k: v for k, v in entry.items() if k not in _META}
    if kind == "auto":
        kind = cached_platform(entry["name"])
        if not kind or kind == "unknown":
            if fetcher is None:
                raise ValueError(f"{entry['name']}: platform unknown; run `devicescout check {entry['name']}`")
            report = detect(fetcher, entry["base_url"])
            remember(entry["name"], report)
            kind = report["platform"]
            log.info("[%s] detected %s (%s)", entry["name"], kind, report["evidence"])
        if kind == "unknown":
            kind = "jsonld"  # best effort; `check` will tell the user it found nothing
    if kind == "gsmarena":
        return GSMArenaSource()
    if kind == "daraz":
        return DarazSource(**opts)
    if kind == "shopify":
        return ShopifySource(**opts)
    if kind == "woocommerce":
        return WooCommerceSource(**opts)
    if kind == "jsonld":
        fields = SiteConfig.__dataclass_fields__
        return GenericSource(SiteConfig(**{k: v for k, v in opts.items() if k in fields}))
    raise ValueError(f"{entry['name']}: unknown type {kind!r}")
