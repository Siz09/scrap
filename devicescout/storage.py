"""SQLite store. One row per *model* (variants merged by canonical key), many offers per model.

Specs from multiple sources are merged: a value already present wins unless the new
source has higher priority (spec databases beat retailer listings, which are often
incomplete or wrong).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Category, Offer, Product
from .normalize import canonical_key
from .pricing import flag_suspicious

SOURCE_PRIORITY = {"gsmarena": 10}  # everything else defaults to 0

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    brand TEXT,
    category TEXT NOT NULL,
    specs TEXT NOT NULL,            -- JSON of canonical specs
    spec_sources TEXT NOT NULL,     -- JSON {spec_key: source} for provenance
    raw_specs TEXT NOT NULL,        -- JSON {source: {label: value}}
    rating REAL,
    review_count INTEGER,
    image TEXT,
    primary_source TEXT,
    primary_url TEXT,
    gtin TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS offers (
    product_key TEXT NOT NULL REFERENCES products(key),
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    variant TEXT NOT NULL DEFAULT '',
    price REAL,
    currency TEXT,
    in_stock INTEGER,
    scraped_at TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT 'np',
    seller TEXT,
    official INTEGER,
    original_price REAL,
    PRIMARY KEY (product_key, url, variant, scraped_at)
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_gtin ON products(gtin);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path = "devicescout.db"):
        # check_same_thread=False: the web server hands a Store to worker threads; each request
        # still opens its own Store, so a connection is never used by two threads at once.
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def _key_for(self, p: Product) -> str:
        # A barcode match beats any name heuristic.
        if p.gtin:
            row = self.db.execute("SELECT key FROM products WHERE gtin = ?", (p.gtin,)).fetchone()
            if row:
                return row["key"]
        return canonical_key(p.brand, p.name)

    def upsert(self, p: Product) -> str:
        key = self._key_for(p)
        row = self.db.execute("SELECT * FROM products WHERE key = ?", (key,)).fetchone()
        prio = SOURCE_PRIORITY.get(p.source, 0)

        if row is None:
            specs, spec_sources = dict(p.specs), {k: p.source for k in p.specs}
            raw = {p.source: p.raw_specs}
            category, name, brand = p.category, p.name, p.brand
            rating, reviews, image = p.rating, p.review_count, p.image
            primary_source, primary_url = p.source, p.url
        else:
            specs = json.loads(row["specs"])
            spec_sources = json.loads(row["spec_sources"])
            raw = json.loads(row["raw_specs"])
            raw[p.source] = p.raw_specs
            for k, v in p.specs.items():
                old_prio = SOURCE_PRIORITY.get(spec_sources.get(k, ""), 0)
                if k not in specs or prio > old_prio:
                    specs[k], spec_sources[k] = v, p.source
            category = Category(row["category"])
            if category == Category.UNKNOWN:
                category = p.category
            use_new = prio > SOURCE_PRIORITY.get(row["primary_source"], 0)
            name = p.name if use_new else row["name"]
            brand = row["brand"] or p.brand
            primary_source = p.source if use_new else row["primary_source"]
            primary_url = p.url if use_new else row["primary_url"]
            # Keep the rating with more reviews behind it.
            if p.rating is not None and (p.review_count or 0) >= (row["review_count"] or 0):
                rating, reviews = p.rating, p.review_count
            else:
                rating, reviews = row["rating"], row["review_count"]
            image = row["image"] or p.image
        gtin = p.gtin or (row["gtin"] if row is not None else None)

        self.db.execute(
            """INSERT INTO products (key, name, brand, category, specs, spec_sources, raw_specs,
                                     rating, review_count, image, primary_source, primary_url, gtin)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET name=excluded.name, brand=excluded.brand,
                 category=excluded.category, specs=excluded.specs, spec_sources=excluded.spec_sources,
                 raw_specs=excluded.raw_specs, rating=excluded.rating, review_count=excluded.review_count,
                 image=excluded.image, primary_source=excluded.primary_source,
                 primary_url=excluded.primary_url, gtin=excluded.gtin, updated_at=CURRENT_TIMESTAMP""",
            (key, name, brand, category.value, json.dumps(specs), json.dumps(spec_sources),
             json.dumps(raw), rating, reviews, image, primary_source, primary_url, gtin),
        )
        for o in p.offers:
            self.db.execute(
                """INSERT OR REPLACE INTO offers (product_key, source, url, variant, price, currency,
                     in_stock, scraped_at, region, seller, official, original_price)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, o.source, o.url, o.variant or "", o.price, o.currency,
                 None if o.in_stock is None else int(o.in_stock), o.scraped_at or _now(),
                 o.region, o.seller, None if o.official is None else int(o.official), o.original_price),
            )
        self.db.commit()
        return key

    def products(self, category: Category | None = None, key: str | None = None) -> list[Product]:
        q, where, args = "SELECT * FROM products", [], []
        if category:
            where.append("category = ?")
            args.append(category.value)
        if key:
            where.append("key = ?")
            args.append(key)
        if where:
            q += " WHERE " + " AND ".join(where)
        out = []
        for row in self.db.execute(q, args).fetchall():
            # Latest offer per (source, url) only; older rows are price history.
            offers = [
                Offer(source=o["source"], url=o["url"], price=o["price"], currency=o["currency"],
                      in_stock=None if o["in_stock"] is None else bool(o["in_stock"]),
                      scraped_at=o["scraped_at"], region=o["region"], seller=o["seller"],
                      official=None if o["official"] is None else bool(o["official"]),
                      variant=o["variant"] or None, original_price=o["original_price"])
                for o in self.db.execute(
                    """SELECT * FROM offers o WHERE product_key = ? AND scraped_at = (
                         SELECT MAX(scraped_at) FROM offers
                         WHERE product_key = o.product_key AND url = o.url AND variant = o.variant)""",
                    (row["key"],),
                ).fetchall()
            ]
            out.append(flag_suspicious(Product(
                source=row["primary_source"], url=row["primary_url"], name=row["name"],
                brand=row["brand"], category=Category(row["category"]),
                specs=json.loads(row["specs"]), offers=offers, rating=row["rating"],
                review_count=row["review_count"], image=row["image"], gtin=row["gtin"],
                key=row["key"], updated_at=row["updated_at"],
            )))
        return out

    def product(self, key: str) -> Product | None:
        found = self.products(key=key)
        return found[0] if found else None

    def spec_sources(self, key: str) -> dict[str, str]:
        row = self.db.execute("SELECT spec_sources FROM products WHERE key = ?", (key,)).fetchone()
        return json.loads(row["spec_sources"]) if row else {}

    def stats(self) -> dict:
        by_cat = {r["category"]: r["n"] for r in self.db.execute(
            "SELECT category, COUNT(*) AS n FROM products GROUP BY category")}
        offers = self.db.execute("SELECT COUNT(*) AS n, MAX(scraped_at) AS last FROM offers").fetchone()
        sources = [r["source"] for r in self.db.execute("SELECT DISTINCT source FROM offers ORDER BY source")]
        return {"products": sum(by_cat.values()), "by_category": by_cat, "offers": offers["n"],
                "last_scraped": offers["last"], "sources": sources}

    def price_history(self, key: str) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT source, url, variant, region, price, currency, scraped_at FROM offers
               WHERE product_key = ? ORDER BY scraped_at""",
            (key,),
        ).fetchall()
