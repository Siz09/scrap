"""Local web app: JSON API + the built Vite frontend, served by one process.

In Docker this is the `web` service: `devicescout serve --host 0.0.0.0 --read-only`.
Read-only removes the scraping endpoints, so visitors can only read; the `scraper`
service fills the shared database on a schedule. Locally, `devicescout serve` runs
on 127.0.0.1 with scraping enabled and opens the browser.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import webbrowser
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .advisor import Needs, advise, needs_from_query
from .query import parse_query
from .jobs import LocalWorker, load_status
from .models import Category, Product
from .paths import sample_db, web_dist
from .sample import build_sample
from .sources import load_entries
from .sources.detect import cached_platform
from .specmeta import meta as spec_meta
from .currency import apply_stored, rates_info
from .storage import Store, open_store

log = logging.getLogger(__name__)


class MustIn(BaseModel):
    key: str
    op: Literal[">=", "<=", "=="] = ">="
    value: float | bool


class NeedsIn(BaseModel):
    category: Category
    budget_min: float | None = None
    budget_max: float | None = None
    uses: dict[str, float] = Field(default_factory=lambda: {"balanced": 1.0})
    os: list[str] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    exclude_brands: list[str] = Field(default_factory=list)
    must: list[MustIn] = Field(default_factory=list)
    official_only: bool = False
    in_stock_only: bool = False
    nepal_only: bool = False       # only devices sold in Nepal (else converted prices abroad count too)
    top: int = Field(default=5, ge=1, le=500)


class AskIn(BaseModel):
    q: str = Field(min_length=1, max_length=300)
    category: Category = Category.PHONE      # used when the text names no device type
    top: int = Field(default=5, ge=1, le=500)


class SourceIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    name: str | None = Field(default=None, max_length=40)
    role: Literal["offers", "reference", "specs", "reviews"] = "offers"
    check: bool = True       # queue a check right away


class SourcePatch(BaseModel):
    enabled: bool


class JobIn(BaseModel):
    kind: Literal["check", "scrape"]
    names: list[str] = Field(default_factory=list)   # empty = all enabled
    limit: int | None = Field(default=None, ge=1)   # None: everything each site has


def _health(st: dict) -> dict:
    """A source's status from whichever is newer: its last check, or its last full update
    (the scraper no longer checks everything first, so an update is usually the evidence)."""
    scraped, checked = st.get("last_scraped_at"), st.get("checked_at")
    if scraped and st.get("check") != "RUNNING" and (not checked or scraped > checked):
        n = st.get("last_scrape_count") or 0
        return {**st, "check": "OK" if n else "FAIL", "checked_at": scraped,
                "check_detail": f"last update saved {n} items" if n else
                                "last update saved nothing: press Check for details"}
    return st


def summary(p: Product) -> dict[str, Any]:
    best = p.best_offer
    return {
        "key": p.key, "name": p.name, "brand": p.brand, "category": p.category.value,
        "image": p.image, "rating": p.rating, "review_count": p.review_count,
        "best_price": p.best_price, "reference_price": p.reference_price_npr,
        "available_in_nepal": p.available_in_nepal,
        # Not sold in Nepal: its cheapest price abroad, converted to NPR at the current rate.
        "converted_price": conv.price_npr if (conv := (None if p.available_in_nepal else p.converted_offer)) else None,
        "converted_from": {"price": conv.price, "currency": conv.currency, "seller": conv.seller} if conv else None,
        "best_seller": best.seller if best else None, "best_official": best.official if best else None,
        # The cheapest price is a price a Nepali tech site lists (e.g. Gadgetbyte), not a shop's.
        "best_listed_only": best.region == "np-ref" if best else None,
        "offer_count": len(p.local_offers()), "specs": p.specs,
        "sources": sorted({o.source for o in p.offers} | ({p.source} if p.source else set())),
    }


def create_app(db: str | Path, sources: str | Path, read_only: bool = False, sample: bool = False,
               admin_key: str | None = None, worker: str = "local") -> FastAPI:
    """Who runs scraping jobs started from the page:
      local     this process (plain `devicescout serve` on your machine)
      external  the scraper container, via the job queue in the shared database (Docker)
      off       nobody: --read-only, or sample data

    The admin key is optional. Without DEVICESCOUT_ADMIN_KEY anyone who can open the page can
    run checks and updates (fine on your own machine). Set it before exposing the site publicly.
    """
    app = FastAPI(title="DeviceScout", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    db = str(db)
    admin_key = admin_key if admin_key is not None else (os.getenv("DEVICESCOUT_ADMIN_KEY") or None)
    if sample or read_only:
        jobs_mode = "off"
    else:
        jobs_mode = "queue" if worker == "external" else "local"
    admin_required = jobs_mode != "off" and bool(admin_key)
    worker_thread = LocalWorker(db, sources) if jobs_mode == "local" else None

    def store() -> Store:
        s = open_store(db)
        try:   # the exchange rates the scraper fetched on its last run
            stored = s.get_kv("fx_rates")
            if stored:
                apply_stored(json.loads(stored))
        except Exception:
            pass
        return s

    @app.get("/api/health", include_in_schema=False)
    def health():
        s = store()
        try:
            s.db.execute("SELECT 1").fetchone()
        finally:
            s.close()
        return {"ok": True, "version": __version__}

    @app.get("/api/meta")
    def get_meta():
        s = store()
        try:
            stats = s.stats()
        finally:
            s.close()
        return {**spec_meta(), "stats": stats, "version": __version__, "read_only": read_only, "sample": sample,
                "jobs_mode": jobs_mode, "admin_required": admin_required, "rates": rates_info()}

    def run_advice(needs: Needs) -> dict:
        s = store()
        try:
            advice = advise(s.products(needs.category), needs)
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            s.close()

        def out(pick):
            return {**pick.to_dict(), **summary(pick.ranked.product), "price_npr": pick.price} if pick else None

        return {"picks": [out(p) for p in advice.picks], "value_pick": out(advice.value_pick),
                "stretch_pick": out(advice.stretch_pick), "considered": advice.considered,
                "excluded": advice.excluded}

    @app.post("/api/parse")
    def post_parse(body: AskIn):
        """Plain words -> structured needs, for the UI to show and let the buyer edit."""
        return parse_query(body.q).to_dict()

    @app.post("/api/ask")
    def post_ask(body: AskIn):
        parsed = parse_query(body.q)
        return {"parsed": parsed.to_dict(), "advice": run_advice(needs_from_query(parsed, body.category, body.top))}

    @app.post("/api/advise")
    def post_advise(body: NeedsIn):
        needs = Needs(
            category=body.category, budget_min=body.budget_min, budget_max=body.budget_max,
            uses=body.uses or {"balanced": 1.0}, os=body.os, brands=body.brands, exclude_brands=body.exclude_brands,
            must={m.key: (m.op, m.value) for m in body.must}, official_only=body.official_only,
            in_stock_only=body.in_stock_only, nepal_only=body.nepal_only, top=body.top,
        )
        return run_advice(needs)

    @app.get("/api/products")
    def list_products(category: Category | None = None, q: str = "", min_price: float | None = None,
                      max_price: float | None = None, sort: Literal["relevance", "price", "-price", "name", "rating"] = "name",
                      priced_only: bool = False, limit: int = Query(60, le=500), offset: int = 0):
        s = store()
        try:
            items = s.products(category)
            if q.strip():
                # Full-text index: matches aliases and chipsets too, best matches first.
                rank = {k: i for i, k in enumerate(s.search(q, category, limit=500))}
                items = sorted((p for p in items if p.key in rank), key=lambda p: rank[p.key])
        finally:
            s.close()
        def price(p: Product) -> float | None:
            """Nepali price, else the price abroad converted to NPR (not sold here yet)."""
            if p.best_price is not None:
                return p.best_price
            conv = p.converted_offer
            return conv.price_npr if conv else None

        if min_price is not None or max_price is not None:
            items = [p for p in items if (v := price(p)) is not None
                     and (min_price is None or v >= min_price) and (max_price is None or v <= max_price)]
        # priced_only (sorting by price): devices with no price anywhere go last, not away.
        keys = {
            "relevance": lambda p: 0,   # keep search order
            "name": lambda p: p.name.lower(),
            "price": lambda p: (price(p) is None, price(p) or 0),
            "-price": lambda p: (price(p) is None, -(price(p) or 0)),
            "rating": lambda p: -(p.rating or 0),
        }
        items.sort(key=keys[sort])
        return {"total": len(items), "items": [summary(p) for p in items[offset:offset + limit]]}

    @app.get("/api/products/{key:path}")
    def get_product(key: str):
        s = store()
        try:
            p = s.product(key)
            if not p:
                raise HTTPException(404, "not found")
            # Chart only offers we trust: a bait listing must not show up as a "price drop".
            flagged = {(o.url, o.variant or "") for o in p.offers if o.suspicious}
            history = [{k: r[k] for k in ("source", "variant", "price", "currency", "scraped_at")}
                       for r in s.price_history(key)
                       if r["region"].startswith("np") and (r["url"], r["variant"]) not in flagged]
            spec_sources = s.spec_sources(key)
        finally:
            s.close()
        offers = sorted(
            ({"seller": o.seller or o.source, "source": o.source, "url": o.url, "price": o.price,
              "currency": o.currency, "price_npr": o.price_npr, "variant": o.variant, "official": o.official,
              "in_stock": o.in_stock, "region": o.region, "suspicious": o.suspicious,
              "original_price": o.original_price, "scraped_at": o.scraped_at,
              "converted": (o.currency or "NPR").upper() not in ("NPR", "RS", "NRS")} for o in p.offers),
            key=lambda o: (o["region"] == "intl", o["suspicious"], o["price_npr"] or 1e12),
        )
        return {**summary(p), "offers": offers, "history": history, "spec_sources": spec_sources,
                "gtin": p.gtin, "updated_at": p.updated_at}

    @app.get("/api/images/{key:path}", include_in_schema=False)
    def get_image(key: str):
        """The device's photo (downloaded once, then served from the data folder), or a drawn
        placeholder when no site has one: every device shows an image."""
        from fastapi.responses import Response

        from .images import image_file, placeholder_svg
        s = store()
        try:
            p = s.product(key)
            if not p:
                raise HTTPException(404, "not found")
            path = image_file(p, remember=lambda url: s.set_image(key, url))
        finally:
            s.close()
        if path:
            return FileResponse(path, headers={"Cache-Control": "public, max-age=604800"})
        return Response(placeholder_svg(p), media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/api/deals")
    def get_deals(category: Category | None = None, verified_only: bool = True,
                  max_price: float | None = None, limit: int = Query(60, le=500)):
        from .deals import find_deals
        s = store()
        try:
            deals = find_deals(s, category, verified_only)
        finally:
            s.close()
        if max_price is not None:
            deals = [d for d in deals if (d.offer.price_npr or 0) <= max_price]
        return {"total": len(deals), "items": [d.to_dict() for d in deals[:limit]]}

    @app.get("/api/quality")
    def get_quality():
        s = store()
        try:
            return s.quality_summary()
        finally:
            s.close()

    def scrapers_info() -> list[dict]:
        """In queue mode the scraper container does the scraping, so report *its* scrapers."""
        from .sources.backends import describe
        if jobs_mode == "local":
            return describe()
        s = store()
        try:
            reported = s.get_kv("worker_scrapers")
        finally:
            s.close()
        return json.loads(reported) if reported else []

    @app.get("/api/sources")
    def get_sources():
        status = load_status()
        out = []
        try:
            entries = load_entries(str(sources))
        except (OSError, ValueError, KeyError) as e:
            return {"sources": [], "file": str(sources), "scrapers": scrapers_info(),
                    "error": f"{sources} can't be read: {type(e).__name__}: {e}"}
        s = store()
        try:
            raw = {r["source"]: r for r in s.raw_summary()}
        finally:
            s.close()
        for e in entries:
            kind = e.get("type", "auto")
            kept = raw.get(e["name"], {})
            out.append({
                "name": e["name"], "enabled": e.get("enabled", True), "role": e.get("role"),
                "region": e.get("region"), "type": kind,
                "platform": cached_platform(e["name"]) if kind == "auto" else kind,
                "verified": e.get("verified", False), "notes": e.get("notes"),
                "url": e.get("base_url") or (f"https://{e['domain']}" if e.get("domain") else None),
                "raw_pages": kept.get("pages", 0), "raw_records": kept.get("records", 0),
                **_health(status.get(e["name"], {})),
            })
        return {"sources": out, "file": str(sources), "scrapers": scrapers_info()}

    def _authorize(key: str | None) -> None:
        if jobs_mode == "off":
            raise HTTPException(403, "sample data: restart without --sample to scrape" if sample else
                                "scraping is disabled on this site (started with --read-only)")
        if admin_required and not (key and hmac.compare_digest(key, admin_key)):
            raise HTTPException(401, "admin key required")

    def _enqueue(kind: str, names: list[str], limit: int | None = None) -> dict:
        s = store()
        try:
            if any(j["status"] in ("queued", "running") and j["kind"] == kind and j["names"] == names
                   for j in s.jobs(10)):
                raise HTTPException(409, "the same job is already queued or running")
            job = s.enqueue_job(kind, names, limit, origin="ui")
        finally:
            s.close()
        if worker_thread:
            worker_thread.wake()
        return job

    @app.post("/api/sources")
    def add_source(body: SourceIn, x_admin_key: str | None = Header(default=None)):
        """Add a store by its link; its platform is detected on the first check."""
        _authorize(x_admin_key)
        from .sources import new_entry, save_entries
        try:
            entry = new_entry(body.url, body.name, body.role)
        except ValueError as e:
            raise HTTPException(400, str(e))
        entries = load_entries(str(sources))
        if any(e.get("base_url", "").rstrip("/") == entry["base_url"] for e in entries):
            raise HTTPException(409, f"{entry['base_url']} is already in the list")
        taken = {e["name"] for e in entries}
        base_name, n = entry["name"], 2
        while entry["name"] in taken:
            entry["name"], n = f"{base_name}-{n}", n + 1
        save_entries(str(sources), entries + [entry])
        job = _enqueue("check", [entry["name"]]) if body.check else None
        return {"source": entry, "job": job}

    @app.patch("/api/sources/{name}")
    def update_source(name: str, body: SourcePatch, x_admin_key: str | None = Header(default=None)):
        _authorize(x_admin_key)
        from .sources import save_entries
        entries = load_entries(str(sources))
        for e in entries:
            if e["name"] == name:
                e["enabled"] = body.enabled
                save_entries(str(sources), entries)
                return e
        raise HTTPException(404, "no such source")

    @app.delete("/api/sources/{name}")
    def delete_source(name: str, x_admin_key: str | None = Header(default=None)):
        _authorize(x_admin_key)
        from .sources import save_entries
        entries = load_entries(str(sources))
        kept = [e for e in entries if e["name"] != name]
        if len(kept) == len(entries):
            raise HTTPException(404, "no such source")
        save_entries(str(sources), kept, removed=name)
        return {"ok": True, "removed": name}

    @app.post("/api/admin/verify")
    def verify_admin(x_admin_key: str | None = Header(default=None)):
        _authorize(x_admin_key)
        return {"ok": True}

    @app.post("/api/jobs")
    def start_job(body: JobIn, x_admin_key: str | None = Header(default=None)):
        _authorize(x_admin_key)
        return _enqueue(body.kind, body.names, body.limit)

    @app.get("/api/jobs")
    def list_jobs():
        s = store()
        try:
            return {"jobs": s.jobs(20), "worker_seen_at": s.get_kv("worker_heartbeat"), "jobs_mode": jobs_mode}
        finally:
            s.close()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        s = store()
        try:
            job = s.job(job_id)
        finally:
            s.close()
        if not job:
            raise HTTPException(404, "no such job")
        return job

    @app.post("/api/jobs/cancel")
    def cancel_job(x_admin_key: str | None = Header(default=None), job_id: str | None = None):
        _authorize(x_admin_key)
        s = store()
        try:
            return {"ok": True, "cancelled": s.request_cancel(job_id)}
        finally:
            s.close()

    dist = web_dist()
    if dist:
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            f = (dist / path).resolve()
            if path and f.is_file() and dist.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")  # client-side routes
    else:
        @app.get("/", include_in_schema=False)
        def no_ui():
            return JSONResponse({"detail": "Frontend not built. Run `npm run build` in web/, or use the API at /api/docs."})

    return app


def serve(host: str = "127.0.0.1", port: int = 8765, db: str | Path = "", sources: str | Path = "",
          open_browser: bool = True, read_only: bool = False, sample: bool = False, worker: str = "local") -> None:
    import uvicorn

    if sample:
        db = build_sample(sample_db())
        print(f"Using the fictional sample catalogue ({db}). Nothing here is a real price.", flush=True)
    app = create_app(db, sources, read_only=read_only, sample=sample, worker=worker)
    if not (read_only or sample) and host not in ("127.0.0.1", "localhost") and not os.getenv("DEVICESCOUT_ADMIN_KEY"):
        print("Note: anyone who can reach this site can start scraping. Set DEVICESCOUT_ADMIN_KEY "
              "before exposing it to the internet.", flush=True)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    print(f"DeviceScout {__version__} running at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
