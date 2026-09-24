"""Check and scrape runs, shared by the CLI and the web app (which runs them in the background)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

from .paths import source_status
from .pipeline import IngestStats, ingest
from .sources import Fetcher, build, load_entries
from .sources.detect import detect, remember
from .storage import Store

Log = Callable[[str], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_status() -> dict:
    try:
        return json.loads(source_status().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_status(name: str, **fields) -> None:
    data = load_status()
    data[name] = {**data.get(name, {}), **fields}
    source_status().write_text(json.dumps(data, indent=2))


def crawl_entry(entry: dict, fetcher, limit: int):
    if entry.get("delay") and hasattr(fetcher, "host_delay"):
        from urllib.parse import urlparse
        host = entry.get("domain") or urlparse(entry.get("base_url", "")).netloc
        if host:
            fetcher.host_delay[host] = float(entry["delay"])
    source = build(entry, fetcher)
    if entry.get("type") == "gsmarena":
        brands = entry.get("brands", ["samsung"])
        per_brand = max(1, limit // max(1, len(brands)))
        for brand in brands:
            yield from source.crawl(fetcher, limit=per_brand, brand=brand, pages=entry.get("pages", 1))
    else:
        yield from source.crawl(fetcher, limit=limit)


Progress = Callable[..., None]


def _noop(**_) -> None:
    pass


def _reset_stats(fetcher) -> None:
    if hasattr(fetcher, "stats"):
        fetcher.stats = {}   # per-source numbers, not a running total across sources


def run_check(entries: list[dict], log: Log = print, sample: int = 3, delay: float = 1.5,
              fetcher_factory=Fetcher, cancel: threading.Event | None = None,
              progress: Progress = _noop) -> list[dict]:
    """Detect each source's platform and pull a few products; report what works.

    OK       products with prices (or, for spec/review sources, with specs)
    PARTIAL  pages were found but without the data this source is for
    FAIL     nothing usable
    """
    results = []
    with fetcher_factory(delay=delay) as fetcher:
        for i, e in enumerate(entries):
            if cancel and cancel.is_set():
                log("cancelled")
                break
            progress(done=i, total=len(entries), current=e["name"])
            _save_status(e["name"], check="RUNNING", checked_at=_now())
            _reset_stats(fetcher)
            status, detail = "FAIL", ""
            try:
                if e.get("type", "auto") == "auto":
                    report = detect(fetcher, e["base_url"], e.get("start_urls"), e.get("region", "np"))
                    remember(e["name"], report)
                    detail = f"{report['platform']}: {report['evidence']}"
                got = []
                for p in crawl_entry(e, fetcher, limit=sample):
                    got.append(p)
                    if len(got) >= sample:
                        break
                if got:
                    priced = sum(1 for p in got if p.offers and p.offers[0].price)
                    specd = sum(1 for p in got if p.specs)
                    wants_prices = e.get("role", "offers") in ("offers", "reference")
                    useful = priced if wants_prices else specd
                    status = "OK" if useful else "PARTIAL"
                    detail += f" | {len(got)} products, {priced} priced, {specd} with specs; e.g. {got[0].name[:40]!r}"
                    if not useful:
                        detail += " | found pages but no " + ("prices" if wants_prices else "specs")
                else:
                    detail += " | no products extracted"
                if hasattr(fetcher, "summary"):
                    detail += f" | via {fetcher.summary()}"
            except Exception as ex:
                detail += f" | {type(ex).__name__}: {str(ex)[:160]}"
            detail = detail.strip(" |")
            _save_status(e["name"], check=status, check_detail=detail, checked_at=_now())
            results.append({"name": e["name"], "status": status, "detail": detail})
            log(f"{e['name']:<15} {status:<7} {detail}")
        progress(done=len(results), total=len(entries), current=None)
    return results


def run_scrape(entries: list[dict], db_path, log: Log = print, limit: int = 300, delay: float = 2.0,
               mode: str = "static", respect_robots: bool = True, fetcher_factory=Fetcher,
               cancel: threading.Event | None = None, verbose: bool = False,
               progress: Progress = _noop) -> dict[str, int]:
    store = Store(db_path)
    counts: dict[str, int] = {}
    try:
        with fetcher_factory(mode=mode, delay=delay, respect_robots=respect_robots) as fetcher:
            for i, e in enumerate(entries):
                n = 0
                stats = IngestStats()
                progress(done=i, total=len(entries), current=e["name"])
                _save_status(e["name"], scrape_running=True)
                _reset_stats(fetcher)
                log(f"{e['name']}: scraping...")
                try:
                    for product in crawl_entry(e, fetcher, limit):
                        if cancel and cancel.is_set():
                            break
                        ingest(store, product, stats)   # raw -> clean -> refine -> index
                        n += 1
                        if verbose or n % 25 == 0:
                            log(f"  {n}: [{product.category.value}] {product.name}")
                except Exception as ex:
                    log(f"{e['name']}: stopped after {n} products ({type(ex).__name__}: {ex})")
                counts[e["name"]] = stats.stored
                _save_status(e["name"], last_scrape_count=stats.stored, last_scraped_at=_now(),
                             last_rejected=stats.rejected, last_fixes=stats.fixes, scrape_running=False)
                log(f"{e['name']}: {stats.line()}")
                if hasattr(fetcher, "summary"):
                    log(f"  scrapers used: {fetcher.summary()}")
                if cancel and cancel.is_set():
                    log("cancelled")
                    break
            progress(done=len(counts), total=len(entries), current=None)
    finally:
        store.close()
    log(f"saved {sum(counts.values())} products")
    return counts


# --- queued jobs -----------------------------------------------------------------

def select_entries(sources_path: str, names: list[str]) -> list[dict]:
    entries = load_entries(sources_path)
    if names:
        return [e for e in entries if e["name"] in set(names)]
    return [e for e in entries if e.get("enabled", True)]


def execute(job: dict, db_path, sources_path, fetcher_factory=Fetcher) -> str:
    """Run one claimed job, streaming its log and progress into the jobs table."""
    store = Store(db_path)
    cancel = threading.Event()

    def log(line: str) -> None:
        if store.job(job["id"])["cancel_requested"]:
            cancel.set()
        store.job_log(job["id"], f"{time.strftime('%H:%M:%S')} {line}")

    def progress(**p) -> None:
        store.job_progress(job["id"], **p)

    status = "done"
    try:
        entries = select_entries(str(sources_path), job["names"])
        if not entries:
            log("no matching sources")
        elif job["kind"] == "check":
            run_check(entries, log=log, progress=progress, cancel=cancel, fetcher_factory=fetcher_factory)
        else:
            run_scrape(entries, db_path, log=log, progress=progress, cancel=cancel,
                       limit=job.get("limit_n") or 300, fetcher_factory=fetcher_factory)
        if cancel.is_set():
            status = "cancelled"
    except Exception as ex:
        log(f"failed: {type(ex).__name__}: {ex}")
        status = "failed"
    finally:
        store.finish_job(job["id"], status)
        store.close()
    return status


class LocalWorker:
    """Runs queued jobs inside the web process (local `devicescout serve`, no scraper container)."""

    def __init__(self, db_path, sources_path):
        self.db_path, self.sources_path = db_path, sources_path
        self._wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="local-worker").start()

    def wake(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        store = Store(self.db_path)
        store.fail_stale_jobs()
        while True:
            job = store.claim_job()
            if job:
                execute(job, self.db_path, self.sources_path)
                continue
            self._wake.wait(5)
            self._wake.clear()
