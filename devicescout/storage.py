"""SQLite store. One row per *model* (variants merged by canonical key), many offers per model.

Specs from multiple sources are merged: a value already present wins unless the new
source has higher priority (spec databases beat retailer listings, which are often
incomplete or wrong).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Category, Offer, Product
from .normalize import canonical_key

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
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS offers (
    product_key TEXT NOT NULL REFERENCES products(key),
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    price REAL,
    currency TEXT,
    in_stock INTEGER,
    scraped_at TEXT,
    PRIMARY KEY (product_key, url, scraped_at)
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
"""


class Store:
    def __init__(self, path: str | Path = "devicescout.db"):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def upsert(self, p: Product) -> str:
        key = canonical_key(p.brand, p.name)
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

        self.db.execute(
            """INSERT INTO products (key, name, brand, category, specs, spec_sources, raw_specs,
                                     rating, review_count, image, primary_source, primary_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET name=excluded.name, brand=excluded.brand,
                 category=excluded.category, specs=excluded.specs, spec_sources=excluded.spec_sources,
                 raw_specs=excluded.raw_specs, rating=excluded.rating, review_count=excluded.review_count,
                 image=excluded.image, primary_source=excluded.primary_source,
                 primary_url=excluded.primary_url, updated_at=CURRENT_TIMESTAMP""",
            (key, name, brand, category.value, json.dumps(specs), json.dumps(spec_sources),
             json.dumps(raw), rating, reviews, image, primary_source, primary_url),
        )
        for o in p.offers:
            self.db.execute(
                "INSERT OR REPLACE INTO offers VALUES (?,?,?,?,?,?,?)",
                (key, o.source, o.url, o.price, o.currency,
                 None if o.in_stock is None else int(o.in_stock), o.scraped_at),
            )
        self.db.commit()
        return key

    def products(self, category: Category | None = None) -> list[Product]:
        q, args = "SELECT * FROM products", ()
        if category:
            q, args = q + " WHERE category = ?", (category.value,)
        out = []
        for row in self.db.execute(q, args).fetchall():
            # Latest offer per (source, url) only; older rows are price history.
            offers = [
                Offer(source=o["source"], url=o["url"], price=o["price"], currency=o["currency"],
                      in_stock=None if o["in_stock"] is None else bool(o["in_stock"]),
                      scraped_at=o["scraped_at"])
                for o in self.db.execute(
                    """SELECT * FROM offers o WHERE product_key = ? AND scraped_at = (
                         SELECT MAX(scraped_at) FROM offers WHERE product_key = o.product_key AND url = o.url)""",
                    (row["key"],),
                ).fetchall()
            ]
            out.append(Product(
                source=row["primary_source"], url=row["primary_url"], name=row["name"],
                brand=row["brand"], category=Category(row["category"]),
                specs=json.loads(row["specs"]), offers=offers, rating=row["rating"],
                review_count=row["review_count"], image=row["image"],
            ))
        return out

    def price_history(self, key: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT source, price, currency, scraped_at FROM offers WHERE product_key = ? ORDER BY scraped_at",
            (key,),
        ).fetchall()
