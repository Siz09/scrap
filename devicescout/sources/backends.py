"""Several scrapers behind one interface, tried in order until one gets through.

Running two libraries over the same page adds nothing; what multiple scrapers buy
you is resilience. Sites block different techniques, so `Fetcher` walks a chain:

  scrapling-http     curl_cffi with a real browser's TLS fingerprint (fast, default)
  urllib             plain Python HTTP, different fingerprint and headers (no deps)
  scrapling-dynamic  real Chromium via Playwright, runs the page's JavaScript
  scrapling-stealth  Camoufox, a hardened Firefox for bot-protected sites
  crawl4ai           optional (pip install crawl4ai), its own browser stack
  firecrawl          optional hosted service (FIRECRAWL_API_KEY), last resort, costs money

A response counts as blocked on 401/403/407/429/503, on known bot-wall markers,
or when JSON was expected and HTML came back; the next backend is then tried.
A 404 is an answer, not a block, so it is never retried elsewhere.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request

from scrapling.parser import Selector

log = logging.getLogger(__name__)

BLOCK_STATUS = {401, 403, 407, 429, 503}
_WALL = re.compile(
    r"cf-chl|challenge-platform|attention required|access denied|are you a robot|captcha|"
    r"px-captcha|_incapsula_|distil_r_captcha|request unsuccessful|verify you are human",
    re.I,
)
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0.0.0 Safari/537.36")


class FetchedPage(Selector):
    """HTML/JSON from a non-Scrapling backend, wrapped so adapters can't tell the difference."""

    def __init__(self, body: bytes, url: str, status: int, backend: str):
        text = body.decode("utf-8", errors="replace")
        super().__init__(text or "<html></html>", url=url)
        self.status = status
        self._bytes = body
        self.backend = backend

    @property
    def body(self) -> bytes:  # type: ignore[override]
        return self._bytes


class NotFound(Exception):
    """The page doesn't exist (404/410): trying other scrapers won't change that."""


def _body_bytes(page) -> bytes:
    b = getattr(page, "body", b"")
    return b if isinstance(b, bytes) else str(b).encode()


def block_reason(page, want_json: bool = False) -> str | None:
    """Why this response looks like a bot wall rather than real content (None = looks fine)."""
    status = getattr(page, "status", 200)
    if status in BLOCK_STATUS:
        return f"HTTP {status}"
    body = _body_bytes(page)
    head = body[:4000].decode("utf-8", errors="replace")
    if want_json:
        stripped = head.lstrip()
        return None if stripped[:1] in ("{", "[") else "expected JSON, got HTML"
    if len(body) < 400 and "<" in head:
        return "near-empty page"
    if _WALL.search(head) and len(body) < 60_000:  # real product pages are big; walls are small
        return "bot-wall page"
    return None


class Backend:
    name = "base"
    browser = False  # browser backends can't return raw JSON bodies reliably

    def available(self) -> bool:
        return True

    def fetch(self, url: str, headers: dict[str, str]):
        raise NotImplementedError

    def close(self) -> None:
        pass


class ScraplingHTTP(Backend):
    name = "scrapling-http"

    def __init__(self):
        self._cm = self._session = None

    def session(self):
        if self._session is None:
            from scrapling.fetchers import FetcherSession
            self._cm = FetcherSession(timeout=30, retries=2)
            self._session = self._cm.__enter__()
        return self._session

    def fetch(self, url, headers):
        return self.session().get(url, headers=headers)

    def close(self):
        if self._cm is not None:
            self._cm.__exit__(None, None, None)
            self._cm = self._session = None


class UrllibHTTP(Backend):
    name = "urllib"

    def fetch(self, url, headers):
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA,
                                                   "Accept-Language": "en-US,en;q=0.9", **headers})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return FetchedPage(r.read(), r.geturl(), r.status, self.name)
        except urllib.error.HTTPError as e:
            return FetchedPage(e.read() or b"", url, e.code, self.name)


class ScraplingDynamic(Backend):
    name = "scrapling-dynamic"
    browser = True

    def available(self):
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def fetch(self, url, headers):
        from scrapling.fetchers import DynamicFetcher
        return DynamicFetcher.fetch(url, headless=True, network_idle=True)


class ScraplingStealth(Backend):
    name = "scrapling-stealth"
    browser = True

    def available(self):
        try:
            import camoufox  # noqa: F401
            return True
        except ImportError:
            return False

    def fetch(self, url, headers):
        from scrapling.fetchers import StealthyFetcher
        return StealthyFetcher.fetch(url, headless=True, network_idle=True)


class Crawl4AI(Backend):
    """Optional. Not installed by default; `pip install crawl4ai && crawl4ai-setup`."""

    name = "crawl4ai"
    browser = True

    def available(self):
        try:
            import crawl4ai  # noqa: F401
            return True
        except ImportError:
            return False

    def fetch(self, url, headers):
        import asyncio

        from crawl4ai import AsyncWebCrawler

        async def run():
            async with AsyncWebCrawler() as crawler:
                return await crawler.arun(url=url)

        result = asyncio.run(run())
        html = (getattr(result, "html", None) or "").encode()
        status = getattr(result, "status_code", None) or (200 if getattr(result, "success", False) else 503)
        return FetchedPage(html, url, status, self.name)


class Firecrawl(Backend):
    """Optional hosted scraper, used last: it costs money per page. Needs FIRECRAWL_API_KEY."""

    name = "firecrawl"
    browser = True

    def available(self):
        if not os.getenv("FIRECRAWL_API_KEY"):
            return False
        try:
            import firecrawl  # noqa: F401
            return True
        except ImportError:
            return False

    def fetch(self, url, headers):
        from firecrawl import Firecrawl as Client

        doc = Client(api_key=os.environ["FIRECRAWL_API_KEY"]).scrape(url, formats=["rawHtml"])
        html = getattr(doc, "raw_html", None) or (doc.get("rawHtml") if isinstance(doc, dict) else "") or ""
        return FetchedPage(html.encode(), url, 200 if html else 503, self.name)


ALL_BACKENDS = [ScraplingHTTP, UrllibHTTP, ScraplingDynamic, ScraplingStealth, Crawl4AI, Firecrawl]


def build_backends(names: list[str] | None = None) -> list[Backend]:
    by_name = {cls.name: cls for cls in ALL_BACKENDS}
    chosen = [by_name[n] for n in names] if names else ALL_BACKENDS
    out = []
    for cls in chosen:
        b = cls()
        if b.available():
            out.append(b)
        else:
            log.info("scraper backend %s not installed; skipping", b.name)
    return out


def describe() -> list[dict]:
    return [{"name": cls.name, "available": cls().available(), "browser": cls.browser} for cls in ALL_BACKENDS]

