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

_META = {"type", "enabled", "role", "verified", "notes", "brands", "added_from_ui", "delay"}


def load_entries(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["sources"]


def save_entries(path: str, entries: list[dict], removed: str | None = None) -> None:
    """Write the source list back, keeping any other top-level keys. Atomic: never a half-written file.
    `removed` is remembered so a shipped default source isn't re-added by a later update."""
    import os
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    doc["sources"] = entries
    if removed:
        doc["removed_by_user"] = sorted(set(doc.get("removed_by_user", [])) | {removed})
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def new_entry(url: str, name: str | None = None, role: str = "offers") -> dict:
    """A source entry for any store URL: the platform is detected on its first check.

    A URL with a path (https://shop.com.np/collections/phones) is also used as the start page,
    so products are found from that category even without a sitemap."""
    import re
    from urllib.parse import urlparse
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("enter a full link starting with https://")
    base = f"{u.scheme}://{u.netloc}"
    slug = name or re.sub(r"^www\.", "", u.netloc).split(".")[0]
    entry = {"name": re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-") or "store", "type": "auto",
             "enabled": True, "role": role, "region": "np" if role in ("offers", "reference") else "intl",
             "verified": False, "base_url": base, "added_from_ui": True}
    if u.path.strip("/"):
        entry["start_urls"] = [url.strip()]
    if role == "reference":
        entry.update({"region": "np-ref", "price_from_text": True, "currency": "NPR"})
    return entry


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
