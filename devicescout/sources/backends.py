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
    looks_html = bool(re.search(r"<(html|body|head)\b", head, re.I))
    if looks_html and len(body) < 400:
        return "near-empty page"      # sitemaps, JSON and robots.txt are legitimately small
    if _WALL.search(head) and len(body) < 60_000:  # real product pages are big; walls are small
        return "bot-wall page"
    return None


_BROWSER_CACHE: dict[str, str | None] = {}


def _browser_missing(module: str) -> str | None:
    """None if `module` (playwright/patchright) can launch its Chromium; else why not. Cached."""
    if module in _BROWSER_CACHE:
        return _BROWSER_CACHE[module]
    reason = None
    try:
        sync_api = __import__(f"{module}.sync_api", fromlist=["sync_playwright"])
        with sync_api.sync_playwright() as p:
            if not os.path.exists(p.chromium.executable_path):
                reason = f"Chromium not installed (run: python -m {module} install chromium)"
    except ImportError:
        reason = f"{module} not installed"
    except Exception as e:  # driver can't start, missing system libraries, ...
        reason = f"{module} can't start: {str(e).splitlines()[0][:100]}"
    _BROWSER_CACHE[module] = reason
    return reason


class Backend:
    name = "base"
    browser = False  # browser backends read JSON from the rendered <pre>, see Fetcher.get

    def missing(self) -> str | None:
        """Why this scraper can't run here (None = ready)."""
        return None

    def available(self) -> bool:
        return self.missing() is None

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

    def missing(self):
        return _browser_missing("playwright")

    def fetch(self, url, headers):
        from scrapling.fetchers import DynamicFetcher
        return DynamicFetcher.fetch(url, headless=True, network_idle=True)


class ScraplingStealth(Backend):
    name = "scrapling-stealth"
    browser = True

    def missing(self):
        # Scrapling's StealthyFetcher drives patchright, a patched Chromium that hides automation.
        return _browser_missing("patchright")

    def fetch(self, url, headers):
        from scrapling.fetchers import StealthyFetcher
        return StealthyFetcher.fetch(url, headless=True, network_idle=True)


class Crawl4AI(Backend):
    """Optional. Not installed by default; `pip install crawl4ai && crawl4ai-setup`."""

    name = "crawl4ai"
    browser = True

    def missing(self):
        try:
            import crawl4ai  # noqa: F401
        except ImportError:
            return "not installed (pip install crawl4ai)"
        return _browser_missing("playwright")

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

    def missing(self):
        try:
            import firecrawl  # noqa: F401
        except ImportError:
            return "not installed (pip install firecrawl-py)"
        if not os.getenv("FIRECRAWL_API_KEY"):
            return "needs FIRECRAWL_API_KEY (paid hosted service)"
        return None

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
    out = []
    for cls in ALL_BACKENDS:
        missing = cls().missing()
        out.append({"name": cls.name, "available": missing is None, "browser": cls.browser, "missing": missing})
    return out


def json_from_rendered(page) -> bytes | None:
    """Browsers show a JSON response as text inside <pre>; recover the raw JSON."""
    try:
        text = page.css("pre::text").get() or page.css("body").first.get_all_text()
    except Exception:
        return None
    text = (text or "").strip()
    if text[:1] in ("{", "["):
        try:
            json.loads(text)
            return text.encode()
        except ValueError:
            return None
    return None

