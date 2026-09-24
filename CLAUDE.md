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
scrapes straight away on start), `db` (postgres:17 with pg_stat_statements, lz4). Every port listens on
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

**Matching one model across sites** (`normalize.canonical_key(brand, name, category)`)
- Drops storage/colour/"5G"/warranty noise from the title.
- For phones, tablets and watches it also cuts the sales pitch after the model (`model_name`: from the first
  "mAh / MP / inch / Snapdragon / Triple Camera / Features and Specs" onwards), and splits "CE5" into "CE 5".
- Other categories keep their numbers, because there the numbers are the model (power banks).
- A merged card keeps the plainest title. After changing these rules, run `devicescout reprocess` to rebuild
  the catalogue from raw records; nothing needs scraping again.

**Jobs** (`jobs.py`, `cli.py cmd_schedule`)
- **One job at a time, one website at a time** (owner's request). The scheduled run and the Check/Update
  jobs started from the website share one queue, taken oldest first. A job started from the page waits
  its turn; the page shows "Now: …" and "#n in line: …".
- Sources are scraped **top to bottom in `sources.json` order** (`scrape_order` keeps the list order).
- On start, the scraper marks jobs left "running" as failed, clears stale flags, and cancels scheduled
  jobs still waiting from the previous container.
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
3. **Old junk itti records** (category pages saved as products before the crawl fix) are still in the
   owner's DB. The owner was offered a "re-parse stored raw pages" command but hasn't asked for it.
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
