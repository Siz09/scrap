"""PostgreSQL store, used by the Docker deployment. Same interface as the SQLite `Store`.

Three layers, one schema each:

  raw        everything as it came off each website, kept forever
             raw.pages    every page/API response fetched (body compressed), per website
             raw.records  every product record as parsed, before cleaning, per website
             Both tables are partitioned by source: each website gets its own partition
             (raw.pages_hukut, raw.records_hukut, ...), so one site can be inspected,
             re-parsed, exported or dropped without touching the others.
  clean      the merged catalogue: products (one row per model, every source's spec values
             kept), offers (every price ever seen), aliases, quality_issues, search_index
  category   one view per device type with typed columns (category.phone.ram_gb is a
             number, category.laptop.has_dedicated_gpu a boolean) plus the lowest current
             price in Nepal: query it like a spreadsheet, or connect a BI tool to it.
  ops        the job queue and small settings shared by the website and the scraper.

Extensions: pg_trgm (typo-tolerant search, "galxy s24"), unaccent (accent-insensitive
search), btree_gin (combined indexes), pg_stat_statements (query timings, if preloaded).

The catalogue logic (merging, spec resolution) is inherited from `Store`; this class only
swaps the database: a small adapter runs Store's SQL on psycopg, and the methods whose SQL
differs (raw layer, search, job claiming) are overridden.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime
from functools import lru_cache

from .models import SPEC_KEYS, Category
from .storage import Store, _now, product_from_payload, record_hash

log = logging.getLogger(__name__)

SCHEMA_VERSION = "5"

EXTENSIONS = ("pg_trgm", "unaccent", "btree_gin")

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS clean;
CREATE SCHEMA IF NOT EXISTS category;
CREATE SCHEMA IF NOT EXISTS ops;

-- unaccent() isn't IMMUTABLE, so it can't be used in an index or generated column directly.
CREATE OR REPLACE FUNCTION clean.f_unaccent(text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
    AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;

-- raw: one partition per website ------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.pages (
    id bigserial,
    source text NOT NULL,
    url text NOT NULL,
    status int,
    scraper text,                         -- which scraper fetched it (scrapling-http, ...-dynamic)
    content_type text,
    hash text NOT NULL,                   -- sha1 of the body: an unchanged page is stored once
    body text COMPRESSION lz4,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    times_seen int NOT NULL DEFAULT 1,
    PRIMARY KEY (source, id),
    UNIQUE (source, url, hash)
) PARTITION BY LIST (source);

CREATE TABLE IF NOT EXISTS raw.records (
    id bigserial,
    source text NOT NULL,
    url text NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    hash text NOT NULL,
    payload jsonb NOT NULL,               -- the parsed record, before cleaning
    PRIMARY KEY (source, id),
    UNIQUE (source, url, hash)
) PARTITION BY LIST (source);

-- clean: the merged catalogue ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS clean.products (
    key text PRIMARY KEY,
    name text NOT NULL,
    brand text,
    category text NOT NULL,
    specs jsonb NOT NULL,
    spec_sources jsonb NOT NULL,
    raw_specs jsonb NOT NULL,
    spec_candidates jsonb NOT NULL DEFAULT '{}',
    rating float8,
    review_count int,
    image text,
    primary_source text,
    primary_url text,
    gtin text,
    updated_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS products_category ON clean.products (category);
CREATE INDEX IF NOT EXISTS products_gtin ON clean.products (gtin);
CREATE INDEX IF NOT EXISTS products_specs ON clean.products USING gin (specs jsonb_path_ops);

CREATE TABLE IF NOT EXISTS clean.offers (
    product_key text NOT NULL REFERENCES clean.products(key) ON DELETE CASCADE,
    source text NOT NULL,
    url text NOT NULL,
    variant text NOT NULL DEFAULT '',
    price float8,
    currency text,
    in_stock smallint,
    scraped_at timestamptz NOT NULL,
    region text NOT NULL DEFAULT 'np',
    seller text,
    official smallint,
    original_price float8,
    valid_until text,
    PRIMARY KEY (product_key, url, variant, scraped_at)
);
CREATE INDEX IF NOT EXISTS offers_source ON clean.offers (source, scraped_at);

CREATE TABLE IF NOT EXISTS clean.aliases (
    product_key text NOT NULL,
    alias text NOT NULL,
    source text,
    PRIMARY KEY (product_key, alias)
);

CREATE TABLE IF NOT EXISTS clean.quality_issues (
    id bigserial PRIMARY KEY,
    source text, url text, kind text NOT NULL, field text, detail text,
    seen_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS issues_kind ON clean.quality_issues (kind);

CREATE TABLE IF NOT EXISTS clean.search_index (
    key text PRIMARY KEY,
    name text, brand text, aliases text, category text, chipset text,
    doc tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', clean.f_unaccent(coalesce(name, ''))), 'A') ||
        setweight(to_tsvector('simple', clean.f_unaccent(coalesce(brand, ''))), 'B') ||
        setweight(to_tsvector('simple', clean.f_unaccent(coalesce(aliases, ''))), 'B') ||
        setweight(to_tsvector('simple', clean.f_unaccent(coalesce(chipset, ''))), 'C') ||
        setweight(to_tsvector('simple', coalesce(category, '')), 'D')
    ) STORED
);
CREATE INDEX IF NOT EXISTS search_doc ON clean.search_index USING gin (doc);
CREATE INDEX IF NOT EXISTS search_trgm ON clean.search_index
    USING gin ((clean.f_unaccent(lower(coalesce(name, '') || ' ' || coalesce(aliases, '')))) gin_trgm_ops);

-- The current price at each (store page, variant): older rows are price history.
CREATE OR REPLACE VIEW clean.latest_offers AS
SELECT DISTINCT ON (product_key, url, variant) *
FROM clean.offers
ORDER BY product_key, url, variant, scraped_at DESC;

-- ops ----------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ops.jobs (
    id text PRIMARY KEY,
    kind text NOT NULL,
    names jsonb NOT NULL DEFAULT '[]',
    limit_n int,
    origin text,
    status text NOT NULL,
    cancel_requested int NOT NULL DEFAULT 0,
    progress jsonb NOT NULL DEFAULT '{}',
    log jsonb NOT NULL DEFAULT '[]',
    created_at timestamptz NOT NULL, started_at timestamptz, finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS jobs_status ON ops.jobs (status, created_at);
CREATE TABLE IF NOT EXISTS ops.kv (k text PRIMARY KEY, v text);
"""

# Typed columns per category view. Anything not listed is still in the `specs` jsonb column.
CATEGORY_COLUMNS: dict[str, tuple[str, ...]] = {
    "phone": ("os", "chipset", "ram_gb", "storage_gb", "display_size_in", "refresh_rate_hz", "resolution_px",
              "main_camera_mp", "camera_count", "optical_zoom_x", "has_ois", "battery_mah", "charging_w",
              "weight_g", "water_resistance", "has_5g", "has_nfc", "os_upgrades", "release_year",
              "expert_score", "benchmark_score"),
    "tablet": ("os", "chipset", "ram_gb", "storage_gb", "display_size_in", "refresh_rate_hz", "resolution_px",
               "main_camera_mp", "battery_mah", "charging_w", "weight_g", "has_5g", "os_upgrades",
               "release_year", "expert_score"),
    "laptop": ("os", "chipset", "gpu", "has_dedicated_gpu", "ram_gb", "storage_gb", "display_size_in",
               "refresh_rate_hz", "resolution_px", "battery_wh", "charging_w", "weight_g", "ports",
               "release_year", "expert_score", "benchmark_score"),
    "smartwatch": ("os", "display_size_in", "battery_mah", "water_resistance", "has_gps", "has_nfc",
                   "weight_g", "release_year", "expert_score"),
    "earbuds": ("battery_mah", "charging_w", "water_resistance", "weight_g", "release_year", "expert_score"),
    "power_bank": ("capacity_mah", "output_w", "ports", "weight_g"),
    "charger": ("output_w", "ports", "weight_g"),
}

_SAFE_IDENT = re.compile(r"[^a-z0-9_]+")


def _column(key: str) -> str:
    kind = SPEC_KEYS.get(key, str)
    path = f"p.specs->'{key}'"
    if kind is bool:
        return f"CASE WHEN jsonb_typeof({path}) = 'boolean' THEN ({path})::boolean END AS {key}"
    if kind in (int, float):
        cast = "int" if kind is int else "float8"
        return f"CASE WHEN jsonb_typeof({path}) = 'number' THEN (p.specs->>'{key}')::numeric::{cast} END AS {key}"
    return f"p.specs->>'{key}' AS {key}"


def category_views_sql() -> str:
    """One view per category: typed spec columns, current Nepali price range, number of sellers."""
    parts = ["DROP SCHEMA IF EXISTS category CASCADE;", "CREATE SCHEMA category;"]
    for cat in Category:
        cols = CATEGORY_COLUMNS.get(cat.value, ())
        select = ",\n  ".join(["p.key", "p.name", "p.brand"] + [_column(c) for c in cols] + [
            "lo.lowest_price_npr", "lo.highest_price_npr", "lo.sellers", "lo.last_seen_at",
            "p.rating", "p.review_count", "p.image", "p.primary_url AS url", "p.specs", "p.updated_at"])
        parts.append(f"""CREATE VIEW category.{_SAFE_IDENT.sub('_', cat.value)} AS
SELECT
  {select}
FROM clean.products p
LEFT JOIN LATERAL (
  -- Current Nepali prices, without bait listings: with 3+ sellers, a price under 45% of the
  -- median is left out (the same rule the website applies; see pricing.py).
  WITH np AS (
    SELECT o.price, o.seller, o.scraped_at FROM clean.latest_offers o
    WHERE o.product_key = p.key AND o.region = 'np' AND o.currency = 'NPR' AND o.price > 0
  ), m AS (
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY price) AS med, count(*) AS n FROM np
  )
  SELECT min(np.price) AS lowest_price_npr, max(np.price) AS highest_price_npr,
         count(DISTINCT np.seller) AS sellers, max(np.scraped_at) AS last_seen_at
  FROM np, m
  WHERE m.n < 3 OR np.price >= 0.45 * m.med
) lo ON true
WHERE p.category = '{cat.value}';""")
    return "\n".join(parts)


# --- sqlite-flavoured SQL on psycopg ---------------------------------------------------

_UPSERTS = {
    "offers": ("(product_key, url, variant, scraped_at)",
               "source = excluded.source, price = excluded.price, currency = excluded.currency, "
               "in_stock = excluded.in_stock, region = excluded.region, seller = excluded.seller, "
               "official = excluded.official, original_price = excluded.original_price, "
               "valid_until = excluded.valid_until"),
    "kv": ("(k)", "v = excluded.v"),
}


@lru_cache(maxsize=256)
def translate(sql: str) -> str:
    """Store's SQLite statements in PostgreSQL syntax: ? placeholders, INSERT OR IGNORE/REPLACE."""
    out = sql.replace("%", "%%").replace("?", "%s")
    m = re.match(r"\s*INSERT OR (IGNORE|REPLACE) INTO (\w+)", out)
    if m:
        out = out.replace(f"INSERT OR {m.group(1)} INTO", "INSERT INTO", 1)
        if m.group(1) == "IGNORE":
            out += " ON CONFLICT DO NOTHING"
        else:
            target, update = _UPSERTS[m.group(2)]
            out += f" ON CONFLICT {target} DO UPDATE SET {update}"
    return out


def _plain(v):
    # Timestamps come back as ISO strings, like SQLite, so the rest of the app needn't care.
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    if isinstance(v, date):
        return v.isoformat()
    return v


class _Cursor:
    def __init__(self, cur):
        self.cur = cur
        self.rowcount = cur.rowcount

    def _rows(self):
        return [] if self.cur.description is None else self.cur.fetchall()

    def fetchone(self):
        if self.cur.description is None:
            return None
        row = self.cur.fetchone()
        return None if row is None else {k: _plain(v) for k, v in row.items()}

    def fetchall(self):
        return [{k: _plain(v) for k, v in r.items()} for r in self._rows()]

    def __iter__(self):
        return iter(self.fetchall())


class _Conn:
    """The slice of sqlite3.Connection that Store uses."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql: str, args=()):
        return _Cursor(self.conn.execute(translate(sql), tuple(args)))

    def commit(self) -> None:
        pass      # autocommit; multi-statement writes use `with pg.transaction()`

    def close(self) -> None:
        self.conn.close()


def is_postgres(db) -> bool:
    return str(db).startswith(("postgres://", "postgresql://"))


class PgStore(Store):
    _ready: set[str] = set()       # databases whose schema this process already checked

    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import set_json_loads

        self.url = url
        self.pg = psycopg.connect(url, autocommit=True, row_factory=dict_row,
                                  options="-c search_path=clean,raw,ops,public -c TimeZone=UTC")
        # jsonb comes back as its JSON text, like SQLite's TEXT columns (Store json.loads it).
        set_json_loads(lambda data: bytes(data).decode(), self.pg)
        self.db = _Conn(self.pg)
        self._partitions: set[tuple[str, str]] = set()
        if url not in PgStore._ready:
            self._migrate()
            PgStore._ready.add(url)

    # --- schema ----------------------------------------------------------------------

    def _migrate(self) -> None:
        with self.pg.transaction():
            # Web and scraper containers start together: one of them builds the schema.
            self.pg.execute("SELECT pg_advisory_xact_lock(727001)")
            have = self.pg.execute("SELECT to_regclass('ops.kv') AS t").fetchone()["t"]
            version = have and self.pg.execute("SELECT v FROM ops.kv WHERE k = 'schema_version'").fetchone()
            if version and version["v"] == SCHEMA_VERSION:
                return
            for ext in EXTENSIONS:
                self.pg.execute(f"CREATE EXTENSION IF NOT EXISTS {ext} SCHEMA public")
            self.pg.execute(SCHEMA)
            self.pg.execute(category_views_sql())
            self.pg.execute("INSERT INTO ops.kv VALUES ('schema_version', %s) "
                            "ON CONFLICT (k) DO UPDATE SET v = excluded.v", (SCHEMA_VERSION,))
        try:     # optional: query statistics, if the server preloads the library
            self.pg.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements SCHEMA public")
        except Exception:
            pass

    def _partition(self, table: str, source: str) -> None:
        """raw.<table>_<source>: each website's raw data in its own partition."""
        if (table, source) in self._partitions:
            return
        from psycopg import sql
        name = f"{table}_{_SAFE_IDENT.sub('_', source.lower()).strip('_')[:40] or 'x'}"
        with self.pg.transaction():
            self.pg.execute("SELECT pg_advisory_xact_lock(727002)")
            exists = self.pg.execute(
                "SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = %s::regclass AND pg_get_expr(c.relpartbound, c.oid) LIKE %s",
                (f"raw.{table}", f"%('{source}')%")).fetchone()
            if not exists:
                taken = self.pg.execute("SELECT to_regclass(%s) AS t", (f"raw.{name}",)).fetchone()["t"]
                if taken:     # two sources whose names differ only in punctuation
                    name = f"{name}_{hashlib.sha1(source.encode()).hexdigest()[:6]}"
                self.pg.execute(sql.SQL("CREATE TABLE raw.{} PARTITION OF raw.{} FOR VALUES IN ({})").format(
                    sql.Identifier(name), sql.Identifier(table), sql.Literal(source)))
        self._partitions.add((table, source))

    def close(self) -> None:
        self.pg.close()

    # --- writes ----------------------------------------------------------------------

    def upsert(self, p) -> str:
        with self.pg.transaction():
            return super().upsert(p)

    def record_raw(self, p) -> bool:
        self._partition("records", p.source)
        cur = self.pg.execute(
            "INSERT INTO raw.records (source, url, hash, payload) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (p.source, p.url, record_hash(p), json.dumps(p.to_dict(), default=str).replace("\\u0000", "")))
        return cur.rowcount > 0

    def record_page(self, source: str, url: str, status: int | None, scraper: str | None,
                    content_type: str | None, body) -> None:
        """Keep every page fetched, as fetched. Unchanged pages only bump last_seen_at."""
        if isinstance(body, (bytes, bytearray, memoryview)):
            body = bytes(body).decode("utf-8", "replace")
        body = (body or "").replace("\x00", "")
        self._partition("pages", source)
        self.pg.execute(
            """INSERT INTO raw.pages (source, url, status, scraper, content_type, hash, body)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (source, url, hash) DO UPDATE
               SET last_seen_at = now(), times_seen = raw.pages.times_seen + 1,
                   status = excluded.status, scraper = excluded.scraper""",
            (source, url, status, scraper, content_type, hashlib.sha1(body.encode()).hexdigest(), body))

    def raw_records(self):
        last = ("", 0)
        while True:      # keyset pages: the raw layer can be far bigger than memory
            rows = self.pg.execute(
                "SELECT source, id, payload FROM raw.records WHERE (source, id) > (%s, %s) "
                "ORDER BY source, id LIMIT 500", last).fetchall()
            if not rows:
                return
            for row in rows:
                yield product_from_payload(json.loads(row["payload"]))
            last = (rows[-1]["source"], rows[-1]["id"])

    def _raw_count(self) -> int:
        return self.pg.execute("SELECT count(*) AS n FROM raw.records").fetchone()["n"]

    def raw_summary(self) -> list[dict]:
        """Per website: pages and records kept in the raw layer."""
        rows = self.pg.execute(
            """SELECT source, sum(pages)::int AS pages, sum(records)::int AS records,
                      max(last_seen) AS last_seen
               FROM (SELECT source, count(*) AS pages, 0 AS records, max(last_seen_at) AS last_seen
                     FROM raw.pages GROUP BY source
                     UNION ALL
                     SELECT source, 0, count(*), max(fetched_at) FROM raw.records GROUP BY source) t
               GROUP BY source ORDER BY source""").fetchall()
        return [{k: _plain(v) for k, v in r.items()} for r in rows]

    def reset_catalogue(self) -> None:
        self.pg.execute("TRUNCATE clean.offers, clean.products, clean.aliases, clean.search_index, "
                        "clean.quality_issues")

    # --- search ----------------------------------------------------------------------

    def _reindex(self, key, name, brand, category, specs) -> None:
        aliases = " | ".join(r["alias"] for r in self.db.execute(
            "SELECT alias FROM aliases WHERE product_key = ?", (key,)))
        self.pg.execute(
            """INSERT INTO clean.search_index (key, name, brand, aliases, category, chipset)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (key) DO UPDATE SET name = excluded.name, brand = excluded.brand,
                 aliases = excluded.aliases, category = excluded.category, chipset = excluded.chipset""",
            (key, name, brand or "", aliases, category.value.replace("_", " "), str(specs.get("chipset") or "")))

    def search(self, q: str, category: Category | None = None, limit: int = 50) -> list[str]:
        """Full-text prefix search ('s24 ultra'); if that finds nothing, fuzzy matching ('galxy s24')."""
        tokens = re.findall(r"\w+", q.lower())
        if not tokens:
            return []
        cat_sql, cat_args = ("AND category = %s", [category.value.replace("_", " ")]) if category else ("", [])
        tsq = " & ".join(f"{t}:*" for t in tokens)
        rows = self.pg.execute(
            f"""SELECT key FROM clean.search_index, to_tsquery('simple', clean.f_unaccent(%s)) q
                WHERE doc @@ q {cat_sql}
                ORDER BY ts_rank_cd(doc, q) DESC, length(name) LIMIT %s""",
            [tsq, *cat_args, limit]).fetchall()
        if rows:
            return [r["key"] for r in rows]
        text = " ".join(tokens)
        rows = self.pg.execute(
            f"""SELECT key FROM clean.search_index
                WHERE clean.f_unaccent(lower(coalesce(name, '') || ' ' || coalesce(aliases, ''))) %%> %s
                {cat_sql}
                ORDER BY word_similarity(%s, clean.f_unaccent(lower(coalesce(name, '') || ' '
                                         || coalesce(aliases, '')))) DESC
                LIMIT %s""",
            [text, *cat_args, text, limit]).fetchall()
        return [r["key"] for r in rows]

    # --- job queue -------------------------------------------------------------------

    def claim_job(self, job_id: str | None = None, exclude_origin: str | None = None) -> dict | None:
        where, args = self._claim_filter(job_id, exclude_origin)
        row = self.pg.execute(
            f"""UPDATE ops.jobs SET status = 'running', started_at = now()
                WHERE id = (SELECT id FROM ops.jobs WHERE {where.replace('?', '%s')} ORDER BY created_at
                            LIMIT 1 FOR UPDATE SKIP LOCKED)
                RETURNING id""", args).fetchone()
        return self.job(row["id"]) if row else None


def import_sqlite(sqlite_path, pg: PgStore, log=print) -> int:
    """Replay an older SQLite database's raw layer into PostgreSQL (and rebuild the catalogue)."""
    from .pipeline import IngestStats, ingest
    old = Store(sqlite_path)
    stats, n = IngestStats(), 0
    try:
        for p in old.raw_records():
            ingest(pg, p, stats)
            n += 1
    finally:
        old.close()
    log(f"imported {n} raw records from {sqlite_path}: {stats.line()}")
    return n
