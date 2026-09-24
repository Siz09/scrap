"""SQLite store: raw records -> refined catalogue -> search index.

- raw_records keeps every scraped record exactly as parsed, so the catalogue can be
  rebuilt with better parsers later (`devicescout reprocess`) without re-scraping.
- products holds one row per *model* (variants merged by barcode, then canonical key),
  with every source's value for each spec kept in spec_candidates. The value shown is
  resolved by source priority, then by agreement between sources (see resolve_spec).
- offers keeps every price seen, per seller and variant, so history survives.
- quality_issues logs what cleaning rejected or fixed, and where sources disagree.
- search_index is an FTS5 full-text index over names, brands, chipsets and every
  alias a model was listed under ("SM-S928B", "Galaxy S24 Ultra 5G 12/256 Titanium").
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Category, Offer, Product
from .normalize import canonical_key, clean_title
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
CREATE TABLE IF NOT EXISTS raw_records (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    hash TEXT NOT NULL,
    payload TEXT NOT NULL,           -- JSON of the parsed record, before cleaning
    UNIQUE (source, url, hash)
);
CREATE TABLE IF NOT EXISTS quality_issues (
    id INTEGER PRIMARY KEY,
    source TEXT, url TEXT, kind TEXT NOT NULL, field TEXT, detail TEXT,
    seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issues_kind ON quality_issues(kind);
CREATE TABLE IF NOT EXISTS aliases (
    product_key TEXT NOT NULL,
    alias TEXT NOT NULL,
    source TEXT,
    PRIMARY KEY (product_key, alias)
);
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    key UNINDEXED, name, brand, aliases, category, chipset, tokenize = 'unicode61 remove_diacritics 2'
);
"""

# Numeric values within this relative difference count as the same (5000 vs 4900 mAh
# is rounding; 5000 vs 4000 is a real disagreement worth logging).
AGREE = 0.03
CONFLICT = 0.10


def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return a == b
    return abs(a - b) <= AGREE * max(abs(a), abs(b), 1e-9)


def resolve_spec(candidates: dict[str, object]) -> tuple[object, str, bool]:
    """Pick one value from {source: value}: highest-priority source first, then the value
    most sources agree on. Returns (value, source, conflicting)."""
    ranked = sorted(candidates.items(), key=lambda kv: -SOURCE_PRIORITY.get(kv[0], 0))
    top_prio = SOURCE_PRIORITY.get(ranked[0][0], 0)
    top = [(s, v) for s, v in ranked if SOURCE_PRIORITY.get(s, 0) == top_prio]
    votes = [(sum(_same(v, w) for _, w in candidates.items()), i, s, v) for i, (s, v) in enumerate(top)]
    _, _, source, value = max(votes, key=lambda t: (t[0], -t[1]))
    nums = [v for v in candidates.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    conflicting = bool(nums) and (max(nums) - min(nums)) > CONFLICT * max(abs(x) for x in nums)
    return value, source, conflicting


def record_hash(p: Product) -> str:
    """Content hash that ignores scrape timestamps, so an unchanged page isn't stored twice."""
    core = {"name": p.name, "brand": p.brand, "category": p.category.value, "raw": p.raw_specs,
            "specs": p.specs, "rating": p.rating, "reviews": p.review_count,
            "offers": [(o.price, o.currency, o.variant, o.in_stock, o.seller, o.region) for o in p.offers]}
    return hashlib.sha1(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()


def product_from_payload(d: dict) -> Product:
    offers = [Offer(**{k: v for k, v in o.items() if k in Offer.__dataclass_fields__}) for o in d.get("offers", [])]
    fields = {k: v for k, v in d.items() if k in Product.__dataclass_fields__ and k != "offers"}
    fields["category"] = Category(fields.get("category", "unknown"))
    return Product(**fields, offers=offers)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path = "devicescout.db"):
        # check_same_thread=False: the web server hands a Store to worker threads; each request
        # still opens its own Store, so a connection is never used by two threads at once.
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(products)")}
        if "spec_candidates" not in cols:  # databases created before 0.3
            self.db.execute("ALTER TABLE products ADD COLUMN spec_candidates TEXT NOT NULL DEFAULT '{}'")
            self.db.commit()

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

        candidates: dict[str, dict[str, object]] = json.loads(row["spec_candidates"] or "{}") if row else {}
        for k, v in p.specs.items():
            candidates.setdefault(k, {})[p.source] = v
        specs, spec_sources = {}, {}
        for k, cands in candidates.items():
            specs[k], spec_sources[k], conflicting = resolve_spec(cands)
            if conflicting and p.source in cands and len(cands) > 1:
                self.log_issue(p.source, p.url, "conflict", k,
                               "sources disagree: " + ", ".join(f"{s}={v}" for s, v in cands.items()))
        if row is None:
            raw = {p.source: p.raw_specs}
            category, name, brand = p.category, p.name, p.brand
            rating, reviews, image = p.rating, p.review_count, p.image
            primary_source, primary_url = p.source, p.url
        else:
            raw = json.loads(row["raw_specs"])
            raw[p.source] = p.raw_specs
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
                                     rating, review_count, image, primary_source, primary_url, gtin,
                                     spec_candidates)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET name=excluded.name, brand=excluded.brand,
                 category=excluded.category, specs=excluded.specs, spec_sources=excluded.spec_sources,
                 raw_specs=excluded.raw_specs, rating=excluded.rating, review_count=excluded.review_count,
                 image=excluded.image, primary_source=excluded.primary_source,
                 primary_url=excluded.primary_url, gtin=excluded.gtin,
                 spec_candidates=excluded.spec_candidates, updated_at=CURRENT_TIMESTAMP""",
            (key, name, brand, category.value, json.dumps(specs), json.dumps(spec_sources),
             json.dumps(raw), rating, reviews, image, primary_source, primary_url, gtin,
             json.dumps(candidates, default=str)),
        )
        self.db.execute("INSERT OR IGNORE INTO aliases VALUES (?,?,?)", (key, clean_title(p.name), p.source))
        self._reindex(key, name, brand, category, specs)
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

    # --- raw layer, quality log, index ------------------------------------------

    def record_raw(self, p: Product) -> bool:
        """Store the record as parsed. False if this exact content was already stored."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO raw_records (source, url, fetched_at, hash, payload) VALUES (?,?,?,?,?)",
            (p.source, p.url, _now(), record_hash(p), json.dumps(p.to_dict(), default=str)))
        self.db.commit()
        return cur.rowcount > 0

    def raw_records(self):
        for row in self.db.execute("SELECT source, url, payload FROM raw_records ORDER BY id"):
            yield product_from_payload(json.loads(row["payload"]))

    def log_issue(self, source, url, kind, field, detail) -> None:
        self.db.execute("INSERT INTO quality_issues (source, url, kind, field, detail, seen_at) VALUES (?,?,?,?,?,?)",
                        (source, url, kind, field, detail, _now()))

    def quality_summary(self) -> dict:
        by_kind = {r["kind"]: r["n"] for r in self.db.execute(
            "SELECT kind, COUNT(*) AS n FROM quality_issues GROUP BY kind")}
        top = [dict(r) for r in self.db.execute(
            """SELECT source, kind, field, COUNT(*) AS n, MAX(detail) AS example FROM quality_issues
               GROUP BY source, kind, field ORDER BY n DESC LIMIT 25""")]
        raw = self.db.execute("SELECT COUNT(*) AS n FROM raw_records").fetchone()["n"]
        return {"raw_records": raw, "by_kind": by_kind, "top": top}

    def _reindex(self, key, name, brand, category, specs) -> None:
        aliases = " | ".join(r["alias"] for r in self.db.execute(
            "SELECT alias FROM aliases WHERE product_key = ?", (key,)))
        self.db.execute("DELETE FROM search_index WHERE key = ?", (key,))
        self.db.execute("INSERT INTO search_index (key, name, brand, aliases, category, chipset) VALUES (?,?,?,?,?,?)",
                        (key, name, brand or "", aliases, category.value.replace("_", " "),
                         str(specs.get("chipset") or "")))

    def search(self, q: str, category: Category | None = None, limit: int = 50) -> list[str]:
        """Product keys matching free text, best first ('s24 ultra', 'snapdragon 8', 'redmi note')."""
        tokens = re.findall(r"[\w]+", q.lower())
        if not tokens:
            return []
        match = " ".join(f'"{t}"*' for t in tokens)
        sql = "SELECT key FROM search_index WHERE search_index MATCH ?"
        args: list = [match]
        if category:
            sql += " AND category = ?"
            args.append(category.value.replace("_", " "))
        # Column weights: name 10, brand 4, aliases 3, category 1, chipset 2.
        sql += " ORDER BY bm25(search_index, 0, 10, 4, 3, 1, 2) LIMIT ?"
        args.append(limit)
        return [r["key"] for r in self.db.execute(sql, args)]

    def reset_catalogue(self) -> None:
        """Empty the refined layers (not raw_records) before a reprocess."""
        for table in ("products", "offers", "aliases", "search_index", "quality_issues"):
            self.db.execute(f"DELETE FROM {table}")
        self.db.commit()

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
        raw = self.db.execute("SELECT COUNT(*) AS n FROM raw_records").fetchone()["n"]
        issues = self.db.execute("SELECT COUNT(*) AS n FROM quality_issues").fetchone()["n"]
        return {"products": sum(by_cat.values()), "by_category": by_cat, "offers": offers["n"],
                "last_scraped": offers["last"], "sources": sources, "raw_records": raw, "quality_issues": issues}

    def price_history(self, key: str) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT source, url, variant, region, price, currency, scraped_at FROM offers
               WHERE product_key = ? ORDER BY scraped_at""",
            (key,),
        ).fetchall()
