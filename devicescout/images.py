"""Device images: every device shows one.

1. The image a source gave for the model (stores and GSMArena merge per model, so one
   site's photo covers the others).
2. None: look at the device's own pages (the store / review page) for the image the page
   publishes for sharing (og:image, twitter:image, JSON-LD image), remember it.
3. Still none: a generated placeholder (the device type's outline and the model name),
   never presented as a photo.

Images are downloaded once and kept in the data folder (images/), so pages load fast,
still show when a store changes its site, and each store is asked once per image.
Only images belonging to products in the database are fetched.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import time
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .models import Category, Product
from .paths import images_dir

log = logging.getLogger(__name__)

MAX_BYTES = 6 * 1024 * 1024
RETRY_MISSING_AFTER = 3 * 86400          # a device with no image anywhere: look again in 3 days
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
        "image/avif": ".avif", "image/svg+xml": ".svg"}


def _get(url: str, referer: str | None = None, limit: int = MAX_BYTES) -> tuple[bytes, str]:
    headers = {"User-Agent": UA, "Accept": "image/avif,image/webp,image/*,text/html;q=0.8,*/*;q=0.5",
               "Accept-Language": "en-US,en;q=0.9"}
    if referer:
        headers["Referer"] = referer
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
        data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError("too large")
        return data, (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()


def image_from_page(page_html: str, page_url: str) -> str | None:
    """The image a page publishes for sharing: og:image, twitter:image or JSON-LD image."""
    for pat in (r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image(?::src)?)["\'][^>]*'
                r'content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](?:og:image|twitter:image)'):
        m = re.search(pat, page_html, re.I)
        if m:
            return urljoin(page_url, html.unescape(m.group(1).strip()))
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page_html, re.S | re.I):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for obj in data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []:
            img = obj.get("image") if isinstance(obj, dict) else None
            if isinstance(img, list):
                img = img[0] if img else None
            if isinstance(img, dict):
                img = img.get("url")
            if isinstance(img, str) and img:
                return urljoin(page_url, img)
    return None


def _cache_path(url: str) -> Path | None:
    stem = hashlib.sha1(url.encode()).hexdigest()
    for p in images_dir().glob(stem + ".*"):
        return p
    return None


def _download(url: str, referer: str | None) -> Path | None:
    cached = _cache_path(url)
    if cached:
        return cached
    try:
        data, ctype = _get(url, referer)
    except Exception as e:
        log.info("image %s: %s", url, e)
        return None
    ext = _EXT.get(ctype)
    if not ext:                                   # some CDNs send octet-stream: trust the extension
        ext = next((e for e in _EXT.values() if urlparse(url).path.lower().endswith(e)), None)
    if not ext or len(data) < 200:
        return None
    path = images_dir() / (hashlib.sha1(url.encode()).hexdigest() + ext)
    path.write_bytes(data)
    return path


def _missing_marker(key: str) -> Path:
    return images_dir() / f"missing-{hashlib.sha1(key.encode()).hexdigest()}"


def find_image(product: Product, pages: list[str]) -> str | None:
    """No image from any source: read the device's own pages for one."""
    marker = _missing_marker(product.key or product.name)
    if marker.exists() and time.time() - marker.stat().st_mtime < RETRY_MISSING_AFTER:
        return None
    for page_url in pages[:3]:
        try:
            body, ctype = _get(page_url, limit=3 * 1024 * 1024)
        except Exception as e:
            log.info("image lookup %s: %s", page_url, e)
            continue
        if "html" not in ctype and not body.lstrip()[:1] == b"<":
            continue
        found = image_from_page(body.decode("utf-8", "replace"), page_url)
        if found:
            return found
    marker.touch()
    return None


def image_file(product: Product, remember) -> Path | None:
    """The cached image file for a product, downloading (and if needed finding) it first.
    `remember(url)` saves an image found on the product's pages."""
    referer = product.url or None
    if product.image:
        got = _download(product.image, referer)
        if got:
            return got
    pages = list(dict.fromkeys([u for u in [product.url, *(o.url for o in product.offers)]
                                if u and u.startswith("http")]))
    found = find_image(product, pages) if pages else None
    if found and found != product.image:
        got = _download(found, pages[0])
        if got:
            remember(found)
            return got
    return None


# --- placeholder ---------------------------------------------------------------------

_SHAPES = {
    Category.PHONE: '<rect x="85" y="30" width="70" height="140" rx="12"/><circle cx="120" cy="155" r="5"/>',
    Category.TABLET: '<rect x="60" y="40" width="120" height="120" rx="10"/>',
    Category.LAPTOP: '<rect x="60" y="55" width="120" height="80" rx="6"/><path d="M40 145h160l-12 14H52z"/>',
    Category.SMARTWATCH: '<rect x="88" y="65" width="64" height="70" rx="14"/><path d="M98 65l6-30h32l6 30M98 135l6 30h32l6-30"/>',
    Category.EARBUDS: '<circle cx="95" cy="95" r="22"/><circle cx="145" cy="95" r="22"/><path d="M95 117v40M145 117v40"/>',
    Category.TV: '<rect x="40" y="45" width="160" height="100" rx="6"/><path d="M100 165h40M120 145v20"/>',
    Category.MONITOR: '<rect x="45" y="45" width="150" height="95" rx="6"/><path d="M105 165h30l-5-25h-20z"/>',
    Category.CAMERA: '<rect x="55" y="70" width="130" height="85" rx="10"/><circle cx="120" cy="112" r="26"/><path d="M85 70l10-16h50l10 16"/>',
    Category.SPEAKER: '<rect x="80" y="35" width="80" height="130" rx="14"/><circle cx="120" cy="120" r="24"/><circle cx="120" cy="65" r="10"/>',
    Category.POWER_BANK: '<rect x="80" y="40" width="80" height="125" rx="12"/><path d="M125 70l-18 32h16l-8 28 20-36h-16z"/>',
    Category.CHARGER: '<rect x="85" y="75" width="70" height="80" rx="10"/><path d="M105 75V50M135 75V50"/>',
}
_DEFAULT_SHAPE = '<rect x="65" y="45" width="110" height="110" rx="14"/>'


def placeholder_svg(product: Product) -> str:
    """A drawn stand-in: the device type's outline and the model name. Not a photo."""
    shape = _SHAPES.get(product.category, _DEFAULT_SHAPE)
    name = html.escape(product.name)
    lines, line = [], ""
    for word in name.split():
        if len(line) + len(word) > 22 and line:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    text = "".join(f'<tspan x="120" dy="{0 if i == 0 else 15}">{t}</tspan>' for i, t in enumerate(lines[:2]))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" role="img" aria-label="No photo yet">'
            f'<rect width="240" height="240" fill="#f1efec"/>'
            f'<g fill="none" stroke="#b9b3ad" stroke-width="5" stroke-linejoin="round" '
            f'transform="translate(0 -8)">{shape}</g>'
            f'<text x="120" y="205" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" '
            f'fill="#77706a">{text}</text></svg>')
