"""Work out which adapter a store needs, so sites can be added by URL alone.

Order: Daraz (by domain) -> Shopify (/products.json) -> WooCommerce Store API ->
schema.org JSON-LD on a product page found via the sitemap. Results are cached in
a JSON file so detection runs once per site.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

from ..paths import detect_cache
from .base import Fetcher
from .generic import GenericSource, SiteConfig, extract_jsonld_product

log = logging.getLogger(__name__)


def detect(fetcher: Fetcher, base_url: str, start_urls: list[str] | None = None, region: str = "intl") -> dict:
    base = base_url.rstrip("/")
    host = urlparse(base).netloc
    report: dict = {"base_url": base, "platform": None, "evidence": ""}

    if "daraz." in host:
        return {**report, "platform": "daraz", "evidence": "daraz domain"}

    try:
        data = fetcher.get_json(f"{base}/products.json?limit=1", fallback="http")
        if isinstance(data, dict) and "products" in data:
            return {**report, "platform": "shopify", "evidence": "/products.json returned products"}
    except Exception as e:
        report["shopify_error"] = str(e)[:120]

    try:
        data = fetcher.get_json(f"{base}/wp-json/wc/store/v1/products?per_page=1", fallback="http")
        if isinstance(data, list):
            return {**report, "platform": "woocommerce", "evidence": "Store API returned a product list"}
    except Exception as e:
        report["woocommerce_error"] = str(e)[:120]

    # Otherwise read product pages directly: sample a few product links from the sitemap or the
    # category pages and see whether a product with a price can be read (JSON-LD, page tags, page data).
    src = GenericSource(SiteConfig(name="_detect", base_url=base, start_urls=list(start_urls or []),
                                   region=region))
    sampled = 0
    for url in src.discover(fetcher):
        sampled += 1
        try:
            page = fetcher.get(url, mode=src.fetch_mode)
            if extract_jsonld_product(page):
                return {**report, "platform": "jsonld", "evidence": f"JSON-LD Product on {url}"}
            p = src.parse(page)
            if p and p.offers:
                return {**report, "platform": "jsonld",
                        "evidence": f"product with a price on {url} (from page data, not JSON-LD)"}
        except Exception as e:
            report["jsonld_error"] = str(e)[:120]
        if sampled >= 3:
            break
    report["platform"] = "unknown"
    report["evidence"] = (f"no product with a price on {sampled} sampled product links" if sampled
                          else "no product links found in the sitemap or on the category pages; "
                               "set start_urls to a category page (even rendered in a browser it showed none)")
    return report


def cached_platform(name: str) -> str | None:
    try:
        return json.loads(detect_cache().read_text()).get(name, {}).get("platform")
    except (OSError, json.JSONDecodeError):
        return None


def remember(name: str, report: dict) -> None:
    try:
        data = json.loads(detect_cache().read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    data[name] = report
    detect_cache().write_text(json.dumps(data, indent=2))
