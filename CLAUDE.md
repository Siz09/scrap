# DeviceScout: context for Claude Code

This file is loaded automatically. It is the handoff from a long cloud session: what the project
is, how it is built, what was verified on the owner's machine, and what is still open.
**Do not assume beyond this; check the code, and ask the owner when something isn't here.**

## The project

DeviceScout helps buyers in **Nepal** choose electronics (phones, laptops, tablets, watches, ...).
It scrapes Nepali stores (hukut, itti, daraz, brother-mart, fatafat-sewa, oliz-store, ...) and
trusted international sites (GSMArena for specs, review sites), cleans and merges the data, and
gives needs- and budget-based advice in a web app.

- **Personal, local-only.** Runs on the owner's Windows PC with Docker Desktop (PowerShell, repo at
  `D:\vscode\scrap`). No CI/CD, no deploy, no public hosting. These were removed on purpose; don't
  add them back.
- Stack: Python (Scrapling for fetching, FastAPI server), PostgreSQL 17, React + Vite + TypeScript
  frontend. The built frontend is **committed** to `devicescout/web/dist` (the Docker image serves it).

## Run it

```powershell
docker compose up -d --build        # site: http://localhost:8765
docker compose logs -f scraper      # scraper progress
docker compose exec scraper devicescout <command>
```

Services (`docker-compose.yml`): `web`, `scraper` (`devicescout schedule --every ${SCRAPE_EVERY:-6h}`,
on start scrapes only sources with no data yet — a restart doesn't repeat scrapes that already ran;
already-scraped sources wait for the next `--every` interval or a manual Update), `db` (postgres:17
with pg_stat_statements, lz4). Every port listens on
127.0.0.1 only. The DB port defaults to **55432**, because 5433 is taken by another project on the owner's
PC. The owner's own `.env` once overrode it to 5433, so check `.env` if the port collides. Volumes:
`data` (sources.json, image cache), `pgdata`.

Useful CLI commands: `sources`, `check`, `scrape`, `inspect URL` (how a page looks to the scraper
plain vs. in a browser, and what the product parser extracts, specs included), `raw` (what the raw layer
holds per site), `reprocess`, `quality`, `scrapers`, `import-sqlite`, `advise`, `ask`, `export`.

## Development

```bash
python -m pytest -q                         # ~150 pass, ~21 skipped (the PostgreSQL tests)
DEVICESCOUT_TEST_PG=postgresql://... python -m pytest -q   # also runs the PostgreSQL tests (~170)
cd web && npm run build                     # typecheck + build into devicescout/web/dist; commit dist
```

- `tests/conftest.py`: currency is offline with fixed rates, and `Fetcher._sleep` is a no-op, so the
  back-off tests don't really wait.
- The fixtures in `tests/fixtures/` are saved pages. Tests never hit real sites.
- To browser-test the UI: `devicescout serve` on a sample DB (`devicescout.sample.build_sample(path)`,
  23 products) plus Playwright.

## Architecture (where things are)

**Sources** (`devicescout/sources/`)
- `base.py` `Fetcher`: a fallback chain of scrapers.
  - Order: scrapling-http → dynamic (browser) → stealth (patchright) → optional Firecrawl.
  - An explicit `mode="dynamic"` puts the browsers first.
  - `page_sink` archives every fetched page to the raw layer.
  - **HTTP 429**: `_fetch_patiently` backs off 30/90/240 s (or honours Retry-After) and doubles the host
    delay. It then raises `RateLimited`, which stops that source for this run instead of hammering it.
- `generic.py` `GenericSource`: used for any store without a dedicated adapter.
  - Reads sitemaps, then a breadth-first walk of the whole site (`_site_crawl`: listing pages,
    pagination, infinite scroll via `scroll_to_end`).
  - `_looks_like_product` and `_is_listing` separate product pages from category pages.
  - `parse()` reads JSON-LD (Product/ProductGroup → one offer per variant), embedded JSON, meta tags,
    spec tables/`<dl>`/**div-based spec rows** (`_div_spec_rows`), and "Label: value" prose.
  - Listings and product pages each switch to the browser once the plain page proves too thin (`_browser_wins`).
- `platforms.py`: Shopify (`/products.json` for the whole store) and WooCommerce. `daraz.py`, `gsmarena.py`.
  GSMArena also gives a foreign "market price abroad" offer.
- `detect.py` and `registry.py`: auto-detect each site's platform. The site list is in
  `devicescout/data/sources.json` (copied to the data volume; `defaults_version` 4).

**Storage** (`storage.py` is SQLite and the base class; `pgstore.py` is PostgreSQL and the one used in Docker)
- `raw.pages` and `raw.records`: every fetched page and every parsed record, **partitioned per website**.
- `clean.products`, `clean.offers`, `clean.aliases`, `clean.quality_issues`, `clean.search_index`
  (tsvector + trigram).
- `category.<type>` views with typed columns; they hide bait prices below 45% of the median.
- `ops.jobs` and `ops.kv`.
- Extensions: pg_trgm, unaccent, btree_gin, pg_stat_statements. `SCHEMA_VERSION` "5".

**Matching one model across sites** (`normalize.model_name`, `canonical_key(brand, name, category)`)
- **`model_name`** cleans a store title into the card name, for every device: listing tails and ® ™ go.
  For phones, tablets and watches it also cuts everything after the model, at the first bracket, comma,
  " - ", or spec/pitch word (mAh, MP, inch/", Snapdragon, Dimensity, AMOLED, Triple/Main Camera,
  Android 15, "Features and Specs", "with …"). It also drops a trailing "Smartphone"/"Mobile".
  "(2024)" years are kept. Earbuds get the same cut. For every device, `clean_title` drops a colon tagline
  ("Honor 600 Lite 5G: Stunning") and page words ("Features", "Overview", "Details"), and `model_name`
  drops "for …" use-case tails except for cases, cables, chargers, accessories and power banks.
- **`canonical_key`** is the merge key.
  - Removes RAM/storage, colours, 4G/5G and warranty text.
  - "+" becomes "plus", so S24+ stays apart from the S24.
  - Glued series numbers are split ("CE5", "iPhone16", "HOT60" become "CE 5", "iPhone 16", "HOT 60").
  - Sub-brands own the key ("Xiaomi Redmi Note 14" = "Redmi Note 14").
  - Family names imply the brand when it's missing (Galaxy → samsung, iPhone → apple).
- Other categories keep their numbers (power banks), and laptop configs aren't merged.
- **`devicescout duplicates`** lists cards that probably still are one device (`likely_same`), for tuning.
- After changing matching/cleaning rules: `devicescout reprocess` (re-cleans the stored records).
- After changing a **page parser**: `devicescout reparse [site ...] [--dry-run] [--force]` (`reparse.py`, PostgreSQL only).
  It re-reads every saved page in `raw.pages` with today's parser (listing pages first, for category hints),
  replaces that site's `raw.records` (prices keep the page's fetch time), then runs `reprocess`.
  A site whose new reading has under 50% of its old records keeps them unless `--force`. Sites without
  saved pages are left alone. Pages only hold what was fetched: Hukut pages fetched before the browser
  change have no spec sheet, so Hukut still needs one re-scrape for specs.

**Jobs** (`jobs.py`, `cli.py cmd_schedule`)
- **One job at a time, one website at a time** (owner's request). The scheduled run and the Check/Update
  jobs started from the website share one queue, taken oldest first. A job started from the page waits
  its turn; the page shows "Now: …" and "#n in line: …".
- Sources are scraped **top to bottom in `sources.json` order** (`scrape_order` keeps the list order).
- On start, the scraper marks jobs left "running" as failed, clears stale flags, and cancels scheduled
  jobs still waiting from the previous container.
- **Full runs resume after a restart** (`run_scrape(resume=True)`, kv `scrape_cycle`): sites finished in the
  run are skipped, and the site it was on skips product pages already fetched in the run (`pages_since`,
  `GenericSource.skip_urls`). A completed run clears the cycle; one older than 48 h starts over.
- While a job runs, a watcher thread (every `WATCH_SECONDS`=20) writes the heartbeat and checks Stop, so a
  quiet browser walk (Hukut) no longer shows "scraper isn't running" and Stop works mid-site.
- `run_scrape` also refreshes exchange rates.

**Prices**
- `currency.py`: live rates from the NRB API, falling back to open.er-api.com; stored in `ops.kv` `fx_rates`.
  `DEVICESCOUT_RATES` pins rates.
- Devices sold only abroad are **shown**, with a converted NPR price labelled "converted" and "Not sold in
  Nepal yet" (`Product.available_in_nepal`, `converted_offer`).

**Advisor** (`advisor.py`): needs, budget and must-haves.
- Lists every match (up to 500; the UI shows the top 5, then "more" 20 at a time).
- Has an "Only sold in Nepal" filter, and says why devices were left out (`excluded`).

**Images** (`images.py`, `/api/images/{key}`): downloads and caches the product image, falls back to
og:image from the product's page, then to an SVG placeholder.

**Frontend** (`web/src/`)
- Pages: Advisor, Browse, Compare, Deals, Sources (data sources, Check/Update, job progress),
  ProductDetail. Components PriceTag and DeviceImage.
- Sources page "Scraping" column, from `source_status.json` fields set in `run_scrape`: `scrape_started_at`,
  `scrape_so_far` (every 25 items), `last_scrape_seconds`, `last_scrape_outcome` (done/stopped/rate limited/error).
  It shows Scraping now / Next up / Waiting · N sites ahead / In line · #n / Done N ago (took …) / never scraped.
- `stored.ts` `useStored`: filters and typed text on Advisor/Browse/Deals are kept in localStorage.
  Advisor has a "Start over" button.

## Git state

- Work happens on branch `claude/zealous-hamilton-nez4j3`. The owner merged it to `main` once
  (PR #4). **About 21 later commits on the branch are not in `main` yet**; the owner merges them on GitHub.
- The cloud session couldn't push to `main`. Locally, do what the owner says.

## Verified on the owner's machine

- hukut, itti, brother-mart, fatafat-sewa, oliz-store and gadgetbyte are found and scraped.
- Hukut listings need the browser. Hukut product pages carry the price in plain HTML
  (JSON-LD ProductGroup with variants).
- NRB live rates work (e.g. 1 USD = Rs 153.19).
- GSMArena returned 429 before the back-off was added (it is now 1 request per 4 s).

## Open / unverified (latest first)

1. **Hukut specs: verified working with `inspect` on the owner's machine; stored data not re-scraped yet.**
   - Hukut's plain HTML has no specs. The browser-rendered page has a `<div id="specification">`
     sheet: one `<h3>SECTION</h3>` per section, then rows of
     `<div class="grid"><div>Label</div><div class="col-span-2">Value</div></div>`.
   - `inspect https://hukut.com/oneplus-15r` found 39 specs in the dynamic run (16 understood).
   - `_div_spec_rows` prefixes labels with their section, as GSMArena's are ("Battery / Type"), and skips
     wrappers holding two rows. That fix (after the owner's run) is tested only on copied markup.
   - Script-heavy product pages with fewer than 5 specs are re-rendered in the browser, and after 2 wins the
     site goes straight to the browser, so Hukut scrapes are slower.
   - Existing Hukut products only get specs after Hukut is updated again (Update on the Data sources page).
   - Possible speed-up: the spec text may also be in the `self.__next_f` script data of the plain page;
     unconfirmed, since only the description was seen there.
   - Check stored coverage:
     ```sql
     WITH r AS (SELECT (SELECT count(*) FROM jsonb_object_keys(payload->'raw_specs')) AS n
                FROM raw.records WHERE source='hukut')
     SELECT count(*), count(*) FILTER (WHERE n>0), round(avg(n),1) FROM r;
     ```
2. **Real product images from stores** haven't been confirmed to load in the owner's UI.
3. **`reparse` on the owner's DB:** the first run replaced brother-mart (946 → 946), then crashed on gadgetbyte's
   saved sitemap (XML declaration), so the catalogue wasn't rebuilt. Fixed: pages are parsed as bytes,
   sitemaps/feeds skipped, and one site's error no longer stops the run. Needs a re-run to confirm;
   the old junk itti records should disappear after it.
4. **A GSMArena flip phone may show its cover-screen size** (4.1") as the main display. This is flagged
   as a spec conflict. It's unconfirmed which phone; ask the owner.
5. Ideas offered but not requested: incremental price-only updates, parallel scraping per site.

## Owner's preferences

- They write informally and quickly, with typos. Infer intent, but confirm anything destructive.
- **Reply style:** lead with the uncomfortable truth or a challenge; no flattery or filler phrases;
  tag claims [Certain] / [Likely] / [Guessing]; disagree with reasons and give an alternative;
  don't fold on pushback without new information.
- Don't scrape Nepali sites from a cloud sandbox; the owner runs live scrapes locally and pastes the output.
- Don't create PRs unless asked.
