"""Work out which adapter a store needs, so sites can be added by URL alone.

Order: Daraz (by domain) -> Shopify (/products.json) -> WooCommerce Store API ->
schema.org JSON-LD on a product page found via the sitemap. Results are cached in
a JSON file so detection runs once per site.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from .base import Fetcher
from .generic import GenericSource, SiteConfig, extract_jsonld_product, sitemap_urls

log = logging.getLogger(__name__)

CACHE = Path(".devicescout_detect.json")


def detect(fetcher: Fetcher, base_url: str) -> dict:
    base = base_url.rstrip("/")
    host = urlparse(base).netloc
    report: dict = {"base_url": base, "platform": None, "evidence": ""}

    if "daraz." in host:
        return {**report, "platform": "daraz", "evidence": "daraz domain"}

    try:
        data = fetcher.get_json(f"{base}/products.json?limit=1")
        if isinstance(data, dict) and "products" in data:
            return {**report, "platform": "shopify", "evidence": "/products.json returned products"}
    except Exception as e:
        report["shopify_error"] = str(e)[:120]

    try:
        data = fetcher.get_json(f"{base}/wp-json/wc/store/v1/products?per_page=1")
        if isinstance(data, list):
            return {**report, "platform": "woocommerce", "evidence": "Store API returned a product list"}
    except Exception as e:
        report["woocommerce_error"] = str(e)[:120]

    # Fall back to JSON-LD: sample a few sitemap URLs that look like products.
    src = GenericSource(SiteConfig(name="_detect", base_url=base))
    sampled = 0
    for url in sitemap_urls(fetcher, base, limit=2000):
        if not src._wanted(url):
            continue
        sampled += 1
        try:
            if extract_jsonld_product(fetcher.get(url)):
                return {**report, "platform": "jsonld", "evidence": f"JSON-LD Product on {url}"}
        except Exception as e:
            report["jsonld_error"] = str(e)[:120]
        if sampled >= 3:
            break
    report["platform"] = "unknown"
    report["evidence"] = (f"no product JSON-LD on {sampled} sampled sitemap URLs" if sampled
                          else "no sitemap product URLs found; set product_link_css/start_urls or fetch_mode=dynamic")
    return report


def cached_platform(name: str) -> str | None:
    try:
        return json.loads(CACHE.read_text()).get(name, {}).get("platform")
    except (OSError, json.JSONDecodeError):
        return None


def remember(name: str, report: dict) -> None:
    try:
        data = json.loads(CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    data[name] = report
    CACHE.write_text(json.dumps(data, indent=2))
