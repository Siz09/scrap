"""Read the saved pages again with today's parsers, and rebuild the catalogue from them.

Every page a scrape fetches is kept in raw.pages exactly as it came off the site. When a parser
improves (a spec sheet it couldn't read, category pages it mistook for products), `reparse`:

  1. re-reads each website's saved pages with that website's current parser
     (listing pages first, so products get their category hint back, as in a crawl);
  2. replaces that website's raw.records with the new reading. Prices keep the time the page
     was fetched, so price history survives. A website whose new reading lost most of its
     records keeps the old ones (a parser regression shouldn't wipe data) unless forced;
  3. rebuilds the clean catalogue from raw.records (pipeline.reprocess).

Websites with no saved pages (scraped before pages were kept) keep their records as they are.
PostgreSQL only: the SQLite store doesn't keep pages.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from .models import Product
from .storage import record_hash

log = logging.getLogger(__name__)

# Keep a website's old records when the new reading has fewer than this share of them.
KEEP_BELOW = 0.5


@dataclass
class SiteResult:
    source: str
    pages: int = 0
    before: int = 0
    after: int = 0
    categories: Counter = field(default_factory=Counter)
    replaced: bool = False
    note: str = ""

    def line(self) -> str:
        cats = ", ".join(f"{c} {n}" for c, n in self.categories.most_common(6))
        verdict = ("replaced" if self.replaced and (self.before or self.after)
                   else "nothing found on these pages" if self.replaced
                   else f"kept old records ({self.note})" if self.note and self.before
                   else self.note or "would be replaced (dry run)")
        return (f"{self.source:<15} {self.pages:>6} pages  records {self.before:>6} -> {self.after:<6} "
                f"{verdict}" + (f"\n{'':<15} {cats}" if cats else ""))


def _pages(pg, source: str, ids: list[int] | None = None) -> Iterator[dict]:
    """A website's saved pages, oldest version first, in batches (bodies can be large)."""
    last = 0
    while True:
        rows = pg.execute(
            "SELECT id, url, content_type, body, last_seen_at FROM raw.pages "
            "WHERE source = %s AND id > %s AND (status IS NULL OR status < 400) "
            + ("AND id = ANY(%s) " if ids is not None else "")
            + "ORDER BY id LIMIT 50",
            (source, last, ids) if ids is not None else (source, last)).fetchall()
        if not rows:
            return
        yield from rows
        last = rows[-1]["id"]


def _json(body: str):
    s = body.lstrip()
    if not s[:1] in ("{", "["):
        return None
    try:
        return json.loads(s)
    except ValueError:
        return None


def _selector(body: str, url: str):
    """A page to parse. Given as bytes: lxml refuses text that starts with an XML encoding
    declaration (<?xml version="1.0" encoding="UTF-8"?>), which some stores send."""
    from scrapling.parser import Selector
    return Selector(body.encode("utf-8"), url=url)


def _not_a_page(url: str, body: str) -> bool:
    """Sitemaps and feeds are saved too (they list the site's pages) but hold no product."""
    head = body.lstrip()[:300].lower()
    return (bool(re.search(r"sitemap[^/]*\.xml|/feed/?$|\.xml(\?|$)", url, re.I))
            or "<urlset" in head or "<sitemapindex" in head or "<rss" in head)


def _stamp(products: list[Product], when: str) -> list[Product]:
    for p in products:
        for o in p.offers:
            o.scraped_at = when          # the price as it was when the page was fetched
    return products


def site_products(src, pg, source: str, progress: Callable[[str], None] = lambda _: None
                  ) -> tuple[int, list[Product]]:
    """(pages read, products) for one website, parsed from its saved pages."""
    from .sources.daraz import DarazSource, extract_items, item_to_product, items_from_html
    from .sources.generic import GenericSource
    from .sources.gsmarena import GSMArenaSource
    from .sources.platforms import ShopifySource, WooCommerceSource

    out: list[Product] = []
    n = 0
    if isinstance(src, GenericSource):
        # Pass 1: listing pages give their products a category hint (as the crawl does).
        product_ids: list[int] = []
        host = urlparse(src._base()).netloc
        for row in _pages(pg, source):
            n += 1
            body = row["body"] or ""
            if _json(body) is not None or _not_a_page(row["url"], body):
                continue
            try:                         # one unreadable page never stops the run
                page = _selector(body, row["url"])
                if src._is_listing(page):
                    words = urlparse(row["url"]).path.replace("-", " ").replace("/", " ")
                    for link in src._links(page, host) + src._embedded_links(page, host):
                        if src._looks_like_product(link):
                            src._hints.setdefault(src._canonical(link, True), words)
                else:
                    product_ids.append(row["id"])
            except Exception as e:
                log.debug("%s: %s", row["url"], e)
            if n % 200 == 0:
                progress(f"  {source}: {n} pages looked at")
        # Pass 2: every other page, read as a product page. (No URL filter: the crawl chose
        # these pages; the site config's URL patterns are for sitemaps, which the walk ignores.)
        for row in _pages(pg, source, product_ids):
            try:
                p = src.parse(_selector(row["body"] or "", row["url"]))
            except Exception as e:
                log.debug("%s: %s", row["url"], e)
                continue
            if p:
                out += _stamp([p], row["last_seen_at"].isoformat(timespec="seconds"))
        return n, out

    for row in _pages(pg, source):
        n += 1
        body, url = row["body"] or "", row["url"]
        when = row["last_seen_at"].isoformat(timespec="seconds")
        data = _json(body)
        got: list[Product] = []
        if data is None and _not_a_page(url, body):
            continue
        try:
            if isinstance(src, ShopifySource) and isinstance(data, dict):
                m = re.search(r"/collections/([^/]+)/products\.json", url)
                got = [p for p in (src.parse_product(x, m.group(1) if m else "") for x in data.get("products") or []) if p]
            elif isinstance(src, WooCommerceSource) and isinstance(data, list):
                got = [p for p in (src.parse_product(x, "") for x in data if isinstance(x, dict)) if p]
            elif isinstance(src, DarazSource):
                q = parse_qs(urlparse(url).query)
                hint = (q.get("q") or [urlparse(url).path.strip("/").replace("-", " ")])[0]
                items = extract_items(data) if data is not None else items_from_html(body)
                got = [p for p in (item_to_product(i, src.base, src.name, hint) for i in items) if p]
            elif isinstance(src, GSMArenaSource) and data is None:
                p = src.parse(_selector(body, url))
                got = [p] if p else []
            elif data is None and hasattr(src, "parse"):
                p = src.parse(_selector(body, url))
                got = [p] if p else []
        except Exception as e:
            log.debug("%s: %s", url, e)
            continue
        out += _stamp(got, when)
        if n % 200 == 0:
            progress(f"  {source}: {n} pages read")
    return n, out


def _replace_records(store, source: str, products: list[Product]) -> int:
    """Swap a website's raw records for the new reading, in one transaction."""
    seen: set[tuple[str, str]] = set()
    rows = []
    for p in products:
        h = record_hash(p)
        if (p.url, h) in seen:           # the same reading from an unchanged page version
            continue
        seen.add((p.url, h))
        when = min((o.scraped_at for o in p.offers if o.scraped_at), default=None)
        rows.append((source, p.url, h, json.dumps(p.to_dict(), default=str).replace("\\u0000", ""), when))
    pg = store.pg
    store._partition("records", source)          # a website that only has pages so far
    with pg.transaction():
        pg.execute("DELETE FROM raw.records WHERE source = %s", (source,))
        for r in rows:
            pg.execute("INSERT INTO raw.records (source, url, hash, payload, fetched_at) "
                       "VALUES (%s, %s, %s, %s, COALESCE(%s::timestamptz, now())) ON CONFLICT DO NOTHING", r)
    return len(rows)


def reparse(store, entries: list[dict], only: list[str] | None = None, dry_run: bool = False,
            force: bool = False, log_line: Callable[[str], None] = print) -> list[SiteResult]:
    """Re-read saved pages for each website (or `only` these) and rebuild the catalogue."""
    from .pipeline import reprocess
    from .sources import build
    from .sources.generic import GenericSource, SiteConfig

    pg = getattr(store, "pg", None)
    if pg is None:
        raise RuntimeError("reparse needs the PostgreSQL store: the SQLite store doesn't keep pages")
    by_name = {e["name"]: e for e in entries}
    kept = {r["source"]: r for r in store.raw_summary()}
    results: list[SiteResult] = []
    for source, counts in sorted(kept.items()):
        if only and source not in only:
            continue
        res = SiteResult(source, before=counts.get("records") or 0)
        results.append(res)
        if not counts.get("pages"):
            res.note = "no saved pages"
            log_line(res.line())
            continue
        entry = by_name.get(source)
        if entry is None:
            res.note = "not in sources.json any more"
            log_line(res.line())
            continue
        try:
            src = build(entry)
        except ValueError:            # platform never detected: the whole-site reader
            fields = SiteConfig.__dataclass_fields__
            src = GenericSource(SiteConfig(**{k: v for k, v in entry.items() if k in fields}))
        log_line(f"{source}: reading saved pages...")
        try:
            res.pages, products = site_products(src, pg, source, log_line)
        except Exception as e:           # one website's trouble doesn't stop the others
            res.note = f"could not read its pages ({type(e).__name__}: {str(e)[:120]})"
            log_line(res.line())
            continue
        latest = {p.url: p for p in products}          # one count per product, not per page version
        res.categories = Counter(p.category.value for p in latest.values())
        res.after = len({(p.url, record_hash(p)) for p in products})
        if res.after == 0 and res.before:
            res.note = "new reading found nothing"
        elif res.before and res.after < KEEP_BELOW * res.before and not force:
            res.note = f"new reading has under {KEEP_BELOW:.0%} of them; --force to replace anyway"
        elif not dry_run:
            res.after = _replace_records(store, source, products)
            res.replaced = True
        log_line(res.line())
    if not dry_run and any(r.replaced for r in results):
        log_line("rebuilding the catalogue from the raw records...")
        log_line(reprocess(store).line())
    return results
