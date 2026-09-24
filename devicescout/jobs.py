"""Check and scrape runs, shared by the CLI and the web app (which runs them in the background)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from .paths import source_status
from .pipeline import IngestStats, ingest
from .sources import Fetcher, build
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
    source = build(entry, fetcher)
    if entry.get("type") == "gsmarena":
        brands = entry.get("brands", ["samsung"])
        per_brand = max(1, limit // max(1, len(brands)))
        for brand in brands:
            yield from source.crawl(fetcher, limit=per_brand, brand=brand, pages=entry.get("pages", 1))
    else:
        yield from source.crawl(fetcher, limit=limit)


def run_check(entries: list[dict], log: Log = print, sample: int = 3, delay: float = 1.5,
              fetcher_factory=Fetcher, cancel: threading.Event | None = None) -> list[dict]:
    """Detect each source's platform and pull a few products; report what works."""
    results = []
    with fetcher_factory(delay=delay) as fetcher:
        for e in entries:
            if cancel and cancel.is_set():
                log("cancelled")
                break
            status, detail = "FAIL", ""
            try:
                if e.get("type", "auto") == "auto":
                    report = detect(fetcher, e["base_url"])
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
                    status = "OK"
                    detail += f" | {len(got)} products, {priced} priced, {specd} with specs; e.g. {got[0].name[:40]!r}"
                    if hasattr(fetcher, "summary"):
                        detail += f" | via {fetcher.summary()}"
                else:
                    detail += " | no products extracted"
            except Exception as ex:
                detail += f" | {type(ex).__name__}: {str(ex)[:160]}"
            detail = detail.strip(" |")
            _save_status(e["name"], check=status, check_detail=detail, checked_at=_now())
            results.append({"name": e["name"], "status": status, "detail": detail})
            log(f"{e['name']:<15} {status:<5} {detail}")
    return results


def run_scrape(entries: list[dict], db_path, log: Log = print, limit: int = 300, delay: float = 2.0,
               mode: str = "static", respect_robots: bool = True, fetcher_factory=Fetcher,
               cancel: threading.Event | None = None, verbose: bool = False) -> dict[str, int]:
    store = Store(db_path)
    counts: dict[str, int] = {}
    try:
        with fetcher_factory(mode=mode, delay=delay, respect_robots=respect_robots) as fetcher:
            for e in entries:
                n = 0
                stats = IngestStats()
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
                             last_rejected=stats.rejected, last_fixes=stats.fixes)
                log(f"{e['name']}: {stats.line()}")
                if hasattr(fetcher, "summary"):
                    log(f"  scrapers used: {fetcher.summary()}")
                if cancel and cancel.is_set():
                    log("cancelled")
                    break
    finally:
        store.close()
    log(f"saved {sum(counts.values())} products")
    return counts


class JobManager:
    """One background job at a time (scraping in parallel would just trip rate limits)."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._cancel = threading.Event()

    def running(self) -> dict | None:
        return next((j for j in self.jobs.values() if j["status"] == "running"), None)

    def start(self, kind: str, fn: Callable[..., object], **kwargs) -> dict:
        with self._lock:
            if self.running():
                raise RuntimeError("another job is running")
            job = {"id": uuid.uuid4().hex[:10], "kind": kind, "status": "running", "log": [],
                   "started_at": _now(), "finished_at": None, "result": None}
            self.jobs[job["id"]] = job
            self._cancel.clear()

        def log(line: str) -> None:
            job["log"].append(f"{time.strftime('%H:%M:%S')} {line}")
            del job["log"][:-500]  # keep the tail

        def run() -> None:
            try:
                job["result"] = fn(log=log, cancel=self._cancel, **kwargs)
                job["status"] = "cancelled" if self._cancel.is_set() else "done"
            except Exception as ex:
                log(f"failed: {type(ex).__name__}: {ex}")
                job["status"] = "failed"
            finally:
                job["finished_at"] = _now()

        threading.Thread(target=run, daemon=True, name=f"job-{job['id']}").start()
        return job

    def cancel(self) -> None:
        self._cancel.set()
