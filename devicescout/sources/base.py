"""Fetching (via Scrapling) and the Source interface every site adapter implements."""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from collections.abc import Iterator
from urllib.parse import urlparse

from ..models import Product
from .backends import FetchedPage, NotFound, UrllibHTTP, block_reason, build_backends, json_from_rendered

log = logging.getLogger(__name__)

USER_AGENT = "DeviceScoutBot/0.1 (+https://github.com/siz09/scrap)"


class Fetcher:
    """Polite fetching through a chain of scrapers (see backends.py).

    Each request goes to the backend that last worked for that host, then falls through
    the rest of the chain when a response looks blocked. Per-host throttling and robots.txt
    apply to every backend alike.

    mode picks where the chain starts: "static" (fast HTTP first), "dynamic" (browser
    first, for JavaScript-heavy sites) or "stealth" (hardened browser first). Only use
    stealth where a site's terms allow automated access.
    """

    def __init__(self, mode: str = "static", delay: float = 2.0, respect_robots: bool = True,
                 backends: list[str] | list | None = None):
        self.mode = mode
        self.delay = delay
        self.respect_robots = respect_robots
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._backends = None
        self._backend_spec = backends
        self._preferred: dict[str, str] = {}     # host -> backend that last got through
        self._broken: set[str] = set()           # backends that crashed (e.g. browser not installed)
        self.stats: dict[str, dict[str, int]] = {}
        self.host_delay: dict[str, float] = {}   # slower pacing for sites that rate-limit (e.g. Daraz)
        self._session = None                     # tests may inject a fake robots.txt session

    @property
    def backends(self) -> list:
        if self._backends is None:
            spec = self._backend_spec
            if spec and not isinstance(spec[0], str):
                self._backends = list(spec)          # already-built backend objects (tests)
            else:
                self._backends = build_backends(spec)
        return self._backends

    def _static(self):
        if self._session is not None:
            return self._session
        http = next((b for b in self.backends if b.name == "scrapling-http"), None)
        return http.session() if http else _UrllibSession()

    def close(self) -> None:
        for b in self._backends or []:
            b.close()

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
            # Fetched through a browser-like session: urllib's own reader gets 403'd by
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
        wait = max(self.delay, self.host_delay.get(host, 0)) - (time.monotonic() - self._last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def _chain(self, host: str, mode: str, want_json: bool) -> list:
        chain = [b for b in self.backends if b.name not in self._broken]
        start = {"dynamic": "scrapling-dynamic", "stealth": "scrapling-stealth"}.get(mode)
        first = self._preferred.get(host) or start
        if first:
            chain.sort(key=lambda b: b.name != first)
        return chain

    def _count(self, backend: str, outcome: str) -> None:
        self.stats.setdefault(backend, {"ok": 0, "blocked": 0, "error": 0})[outcome] += 1

    def get(self, url: str, headers: dict[str, str] | None = None, mode: str | None = None,
            want_json: bool = False, fallback: bool = True):
        """Return a Scrapling Selector-like page (.css(), .urljoin(), .json(), .status, .body).

        fallback=False tries only the first scraper: for cheap probes (platform detection)
        where a refusal is an answer, not something to fight through."""
        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        host = urlparse(url).netloc
        reasons = []
        chain = self._chain(host, mode or self.mode, want_json)
        for backend in chain if fallback else chain[:1]:
            self._throttle(url)
            try:
                page = backend.fetch(url, headers or {})
            except Exception as e:
                self._count(backend.name, "error")
                reasons.append(f"{backend.name}: {type(e).__name__}: {str(e)[:80]}")
                if backend.browser:          # usually "browser not installed": don't retry it all run
                    self._broken.add(backend.name)
                continue
            if getattr(page, "status", 200) in (404, 410):
                raise NotFound(f"HTTP {page.status} for {url}")
            if want_json and backend.browser and getattr(page, "status", 200) < 400:
                raw = json_from_rendered(page)
                if raw is not None:
                    page = FetchedPage(raw, url, 200, backend.name)
            reason = block_reason(page, want_json)
            if reason:
                self._count(backend.name, "blocked")
                reasons.append(f"{backend.name}: {reason}")
                continue
            if getattr(page, "status", 200) >= 400:
                raise RuntimeError(f"HTTP {page.status} for {url}")
            self._count(backend.name, "ok")
            if self._preferred.get(host) != backend.name:
                if reasons:
                    log.info("%s: %s got through after %s", host, backend.name, "; ".join(reasons))
                self._preferred[host] = backend.name
            return page
        raise RuntimeError(f"all scrapers failed for {url}: " + "; ".join(reasons or ["no backend available"]))

    def get_json(self, url: str, headers: dict[str, str] | None = None, fallback: bool = True):
        page = self.get(url, headers={"Accept": "application/json, text/plain, */*", **(headers or {})},
                        want_json=True, fallback=fallback)
        return response_json(page)

    def summary(self) -> str:
        """'scrapling-http 40 ok, urllib 3 ok (2 blocked)' for logs and the UI."""
        parts = []
        for name, s in self.stats.items():
            extra = ", ".join(f"{s[k]} {k}" for k in ("blocked", "error") if s[k])
            parts.append(f"{name} {s['ok']} ok" + (f" ({extra})" if extra else ""))
        return ", ".join(parts) or "no requests"


class _UrllibSession:
    """Minimal .get() for robots.txt when scrapling-http is not in the chain."""

    def get(self, url, **_):
        return UrllibHTTP().fetch(url, {})


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
                page = fetcher.get(url, mode=self.fetch_mode)
                product = self.parse(page)
                if product is None and self.fetch_mode != "dynamic" and _looks_like_js_shell(page):
                    product = self.parse(fetcher.get(url, mode="dynamic"))
            except Exception as e:
                log.warning("[%s] failed %s: %s", self.name, url, e)
                continue
            if product:
                seen += 1
                yield product


def _looks_like_js_shell(page) -> bool:
    """A page that is mostly script with little text: content is rendered by JavaScript."""
    try:
        text = page.css("body").first.get_all_text() if page.css("body") else ""
    except Exception:
        return False
    return len(" ".join(text.split())) < 400


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
