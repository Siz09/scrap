"""Local web app: JSON API + the built Vite frontend, served by one process.

`devicescout serve` (or the desktop executable) runs this on 127.0.0.1 and opens the
browser. `--read-only` removes the scraping endpoints so the same app can be hosted
publicly for buyers while scraping runs elsewhere.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .advisor import Needs, advise, needs_from_query
from .query import parse_query
from .jobs import JobManager, load_status, run_check, run_scrape
from .models import Category, Product
from .paths import sample_db, web_dist
from .sample import build_sample
from .sources import load_entries
from .sources.detect import cached_platform
from .specmeta import meta as spec_meta
from .storage import Store

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
    top: int = Field(default=5, ge=1, le=20)


class AskIn(BaseModel):
    q: str = Field(min_length=1, max_length=300)
    category: Category = Category.PHONE      # used when the text names no device type
    top: int = Field(default=5, ge=1, le=20)


class JobIn(BaseModel):
    kind: Literal["check", "scrape"]
    names: list[str] = Field(default_factory=list)   # empty = all enabled
    limit: int = Field(default=300, ge=1, le=5000)


def summary(p: Product) -> dict[str, Any]:
    best = p.best_offer
    return {
        "key": p.key, "name": p.name, "brand": p.brand, "category": p.category.value,
        "image": p.image, "rating": p.rating, "review_count": p.review_count,
        "best_price": p.best_price, "reference_price": p.reference_price_npr,
        "best_seller": best.seller if best else None, "best_official": best.official if best else None,
        "offer_count": len(p.local_offers()), "specs": p.specs,
        "sources": sorted({o.source for o in p.offers} | ({p.source} if p.source else set())),
    }


def create_app(db: str | Path, sources: str | Path, read_only: bool = False, sample: bool = False) -> FastAPI:
    app = FastAPI(title="DeviceScout", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    jobs = JobManager()
    db = str(db)

    def store() -> Store:
        return Store(db)

    @app.get("/api/meta")
    def get_meta():
        s = store()
        try:
            stats = s.stats()
        finally:
            s.close()
        return {**spec_meta(), "stats": stats, "version": __version__, "read_only": read_only, "sample": sample}

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
            in_stock_only=body.in_stock_only, top=body.top,
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
        if priced_only or min_price is not None or max_price is not None:
            items = [p for p in items if p.best_price is not None
                     and (min_price is None or p.best_price >= min_price)
                     and (max_price is None or p.best_price <= max_price)]
        keys = {
            "relevance": lambda p: 0,   # keep search order
            "name": lambda p: p.name.lower(),
            "price": lambda p: (p.best_price is None, p.best_price or 0),
            "-price": lambda p: (p.best_price is None, -(p.best_price or 0)),
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
              "original_price": o.original_price, "scraped_at": o.scraped_at} for o in p.offers),
            key=lambda o: (o["region"] == "intl", o["suspicious"], o["price_npr"] or 1e12),
        )
        return {**summary(p), "offers": offers, "history": history, "spec_sources": spec_sources,
                "gtin": p.gtin, "updated_at": p.updated_at}

    @app.get("/api/quality")
    def get_quality():
        s = store()
        try:
            return s.quality_summary()
        finally:
            s.close()

    @app.get("/api/sources")
    def get_sources():
        status = load_status()
        out = []
        for e in load_entries(str(sources)):
            kind = e.get("type", "auto")
            out.append({
                "name": e["name"], "enabled": e.get("enabled", True), "role": e.get("role"),
                "region": e.get("region"), "type": kind,
                "platform": cached_platform(e["name"]) if kind == "auto" else kind,
                "verified": e.get("verified", False), "notes": e.get("notes"),
                "url": e.get("base_url") or (f"https://{e['domain']}" if e.get("domain") else None),
                **status.get(e["name"], {}),
            })
        from .sources.backends import describe
        return {"sources": out, "file": str(sources), "scrapers": describe()}

    def _require_writable():
        if read_only:
            raise HTTPException(403, "scraping is disabled on this server (read-only mode)")
        if sample:
            raise HTTPException(409, "running with sample data; restart without --sample to scrape")

    @app.post("/api/jobs")
    def start_job(body: JobIn):
        _require_writable()
        entries = load_entries(str(sources))
        if body.names:
            entries = [e for e in entries if e["name"] in set(body.names)]
        else:
            entries = [e for e in entries if e.get("enabled", True)]
        if not entries:
            raise HTTPException(400, "no matching sources")
        try:
            if body.kind == "check":
                job = jobs.start("check", run_check, entries=entries)
            else:
                job = jobs.start("scrape", run_scrape, entries=entries, db_path=db, limit=body.limit)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return job

    @app.get("/api/jobs")
    def list_jobs():
        return {"jobs": sorted(jobs.jobs.values(), key=lambda j: j["started_at"], reverse=True)[:20]}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        if job_id not in jobs.jobs:
            raise HTTPException(404, "no such job")
        return jobs.jobs[job_id]

    @app.post("/api/jobs/cancel")
    def cancel_job():
        _require_writable()
        jobs.cancel()
        return {"ok": True}

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
          open_browser: bool = True, read_only: bool = False, sample: bool = False) -> None:
    import uvicorn

    if sample:
        db = build_sample(sample_db())
        print(f"Using the fictional sample catalogue ({db}). Nothing here is a real price.", flush=True)
    app = create_app(db, sources, read_only=read_only, sample=sample)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    print(f"DeviceScout {__version__} running at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
