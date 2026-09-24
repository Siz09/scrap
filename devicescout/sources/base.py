"""Fetching (via Scrapling) and the Source interface every site adapter implements."""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from abc import ABC, abstractmethod
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

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlparse(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self._robots:
            rp = urllib.robotparser.RobotFileParser(f"{root}/robots.txt")
            try:
                rp.read()
            except Exception as e:  # unreachable robots.txt: don't block, but say so
                log.warning("robots.txt unreachable for %s (%s); proceeding", root, e)
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

    def get(self, url: str):
        """Return a Scrapling Response (a Selector subclass: .css(), .xpath(), .urljoin())."""
        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        self._throttle(url)
        if self.mode == "stealth":
            from scrapling.fetchers import StealthyFetcher
            page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
        elif self.mode == "dynamic":
            from scrapling.fetchers import DynamicFetcher
            page = DynamicFetcher.fetch(url, headless=True, network_idle=True)
        else:
            from scrapling.fetchers import Fetcher as StaticFetcher
            page = StaticFetcher.get(url, stealthy_headers=True, timeout=30)
        if page.status >= 400:
            raise RuntimeError(f"HTTP {page.status} for {url}")
        return page


class Source(ABC):
    """A site adapter. `discover` yields product URLs; `parse` turns one page into a Product."""

    name: str = "base"
    fetch_mode: str = "static"

    @abstractmethod
    def discover(self, fetcher: Fetcher, **opts) -> Iterator[str]: ...

    @abstractmethod
    def parse(self, page) -> Product | None:
        """`page` is any Scrapling Selector with .url set (live Response or Selector(html, url=...))."""

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


def text_of(node) -> str:
    """Full visible text of a Scrapling element, whitespace-collapsed."""
    if node is None:
        return ""
    return " ".join(node.get_all_text(separator=" ").split())
