"""Fetching (via Scrapling) and the Source interface every site adapter implements."""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from collections.abc import Iterator
from urllib.parse import urlparse

from ..models import Product

log = logging.getLogger(__name__)

USER_AGENT = "DeviceScoutBot/0.1 (+https://github.com/siz09/scrap)"


class Fetcher:
    """Thin, polite wrapper over Scrapling's three fetchers.

    mode:
      "static"  -> scrapling Fetcher (curl_cffi + browser TLS impersonation). Fast; use by default.
      "dynamic" -> DynamicFetcher (Playwright). For JS-rendered listings.
      "stealth" -> StealthyFetcher (Camoufox). For sites with bot protection.
                   Only use it where the site's terms allow automated access.
    """

    def __init__(self, mode: str = "static", delay: float = 2.0, respect_robots: bool = True):
        self.mode = mode
        self.delay = delay
        self.respect_robots = respect_robots
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._session_cm = None
        self._session = None

    def _static(self):
        # One cookie-keeping session per run: some stores (Daraz) set anti-bot cookies
        # on the first HTML hit and reject JSON calls without them.
        if self._session is None:
            from scrapling.fetchers import FetcherSession
            self._session_cm = FetcherSession(timeout=30, retries=2)
            self._session = self._session_cm.__enter__()
        return self._session

    def close(self) -> None:
        if self._session_cm is not None:
            self._session_cm.__exit__(None, None, None)
            self._session_cm = self._session = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlparse(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self._robots:
            # Fetched through the same browser-like session: urllib's own reader gets 403'd by
            # bot walls and then treats the whole site as disallowed.
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                self._throttle(root)
                resp = self._static().get(f"{root}/robots.txt")
                if resp.status >= 400:
                    raise RuntimeError(f"HTTP {resp.status}")
                body = resp.body.decode("utf-8", errors="replace") if isinstance(resp.body, bytes) else str(resp.body)
                rp.parse(body.splitlines())
            except Exception as e:  # unreadable robots.txt: don't block, but say so
                log.warning("robots.txt unreadable for %s (%s); proceeding", root, e)
                rp = None
            self._robots[root] = rp
        rp = self._robots[root]
        return rp is None or rp.can_fetch(USER_AGENT, url)

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        wait = self.delay - (time.monotonic() - self._last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def get(self, url: str, headers: dict[str, str] | None = None, mode: str | None = None):
        """Return a Scrapling Response (a Selector subclass: .css(), .xpath(), .urljoin(), .json())."""
        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        self._throttle(url)
        mode = mode or self.mode
        if mode == "stealth":
            from scrapling.fetchers import StealthyFetcher
            page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
        elif mode == "dynamic":
            from scrapling.fetchers import DynamicFetcher
            page = DynamicFetcher.fetch(url, headless=True, network_idle=True)
        else:
            page = self._static().get(url, headers=headers or {})
        if page.status >= 400:
            raise RuntimeError(f"HTTP {page.status} for {url}")
        return page

    def get_json(self, url: str, headers: dict[str, str] | None = None):
        page = self.get(url, headers={"Accept": "application/json, text/plain, */*", **(headers or {})}, mode="static")
        return response_json(page)


class Source:
    """A site adapter.

    Page-based sources implement `discover` (yield product URLs) and `parse` (one page
    -> Product). API-based sources (Daraz, Shopify, WooCommerce) override `crawl`.
    """

    name: str = "base"
    fetch_mode: str = "static"

    def discover(self, fetcher: Fetcher, **opts) -> Iterator[str]:
        raise NotImplementedError

    def parse(self, page) -> Product | None:
        """`page` is any Scrapling Selector with .url set (live Response or Selector(html, url=...))."""
        raise NotImplementedError

    def crawl(self, fetcher: Fetcher, limit: int = 20, **opts) -> Iterator[Product]:
        seen = 0
        for url in self.discover(fetcher, **opts):
            if seen >= limit:
                return
            try:
                product = self.parse(fetcher.get(url))
            except Exception as e:
                log.warning("[%s] failed %s: %s", self.name, url, e)
                continue
            if product:
                seen += 1
                yield product


def response_json(page):
    """JSON body of a Scrapling response; raises ValueError if the server sent HTML (bot wall)."""
    import json
    body = getattr(page, "body", None)
    if isinstance(body, bytes):
        body = body.decode(getattr(page, "encoding", None) or "utf-8", errors="replace")
    if body is None:
        return page.json()
    body = body.strip()
    if not body or body[0] not in "[{":
        raise ValueError("expected JSON, got HTML (blocked, or not this platform)")
    return json.loads(body)


def text_of(node) -> str:
    """Full visible text of a Scrapling element, whitespace-collapsed."""
    if node is None:
        return ""
    return " ".join(node.get_all_text(separator=" ").split())
