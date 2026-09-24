# DeviceScout

DeviceScout helps buyers in Nepal pick a phone, laptop, tablet, smartwatch or accessory (power banks, chargers, cases, cables, earbuds). You give it what you'll use the device for and your budget in NPR. It returns a short list of devices, explains why each one fits, and says where to buy it and at what price.

It scrapes Nepali stores for prices, including Daraz, Shopify and WooCommerce shops, and stores found by auto-detection. It uses Nepali tech sites for official listed prices, and trusted international sites for specs (GSMArena) and expert reviews (Notebookcheck). It merges these into one record per model. All scraping uses [Scrapling](https://github.com/D4Vinci/Scrapling).

```
sources.json ─► fetch (Scrapling) ─► adapter (Daraz JSON │ Shopify │ WooCommerce │ JSON-LD │ GSMArena)
            ─► normalize specs + NPR prices ─► merge by model / barcode (PostgreSQL) ─► flag fake prices
            ─► advisor: budget + uses + must-haves ─► explained shortlist
```

## Run it (Docker)

A personal tool that runs on your own machine. Three containers: the website, the scraper and a PostgreSQL database. Docker builds the images locally.

```bash
git clone https://github.com/Siz09/scrap.git && cd scrap
docker compose up -d --build
```

Then open http://localhost:8765. Everything listens on `127.0.0.1` only, so nothing is reachable from other devices on your network.

- **web**: the website. On *Data sources* you can:
  - **Check** or **Update** all sources or one at a time. The scraper container runs the job and a live progress bar shows it.
  - **Add a store** by pasting its link.
  - Disable or remove sources.

  Each source also shows how many pages and records have been stored from it.
- **scraper**: scrapes every enabled source completely on start and then every 6 hours (quick stores first) (`SCRAPE_EVERY=12h` in `.env` to change it). It also runs jobs you start from the website within about 5 seconds. Every scraper in the fallback chain is installed:
  - fast HTTP and plain HTTP,
  - Chromium (`scrapling-dynamic`),
  - stealth Chromium (`scrapling-stealth`, patchright),
  - Crawl4AI.

  Firecrawl turns on if you set `FIRECRAWL_API_KEY`; it's a paid hosted service.
- **db** (`postgres:17`): all the data, in three layers (see [Data layers](#data-layers)). Open it with any PostgreSQL tool (DBeaver, pgAdmin, `psql`) at `localhost:55432`, database and user `devicescout`, password `devicescout` (change it with `POSTGRES_PASSWORD` in `.env` before the first start).

If you ran an earlier version, the scraper copies what the old SQLite database collected into PostgreSQL on its first start.

Optional settings go in a `.env` file next to `docker-compose.yml` (see `.env.example`). Docker Compose reads it automatically in any shell (PowerShell, cmd, bash).

Useful commands:

```bash
docker compose logs -f scraper                                   # watch scraping
docker compose exec scraper devicescout raw                      # pages and records kept per website
docker compose exec scraper devicescout quality                  # what cleaning rejected
docker compose exec scraper devicescout reprocess                # rebuild the catalogue after a parser update
docker compose exec scraper devicescout scrapers --test https://example.com   # try every scraper
git pull && docker compose up -d --build                         # update
```

To edit the source list directly, copy it out and back in:

```bash
docker compose cp scraper:/data/sources.json .
docker compose cp ./sources.json scraper:/data/sources.json
```

If the file has a mistake, the *Data sources* page shows the error and the line number.

**Try it without scraping anything.** `docker compose run --rm -p 8765:8765 web devicescout serve --host 0.0.0.0 --no-browser --sample` serves a fictional demo catalogue.

### Development without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
(cd web && npm ci && npm run build)   # or `npm run dev` for hot reload, proxied to the API
pytest
devicescout serve --sample            # local site with a fictional demo catalogue
```

Outside Docker, data lives in `~/.local/share/devicescout` (Linux), `~/Library/Application Support/DeviceScout` (macOS) or `%LOCALAPPDATA%\DeviceScout` (Windows). Set `DEVICESCOUT_HOME` to use a different folder.

## Using the app

- **Find a device.**
  1. Pick a category and a budget in NPR (`45000`, `45k` and `1.5 lakh` all work).
  2. Tap what matters in order of importance: photography, gaming, battery, student, and so on.
  3. Add any must-haves (5G, NFC, minimum RAM, maximum weight...) and an OS.

  Results update as you change the inputs. Each device card shows a fit score, a confidence level, the reasons for its placing, and where to buy it. You'll also see a *Save money* pick and a *Worth stretching?* pick when one of them applies.
- **Deals.** Discounts and price cuts, each checked against other sellers' prices and the recorded price history instead of the store's crossed-out price. Each deal shows:
  - A verdict: *Real deal* (12%+ below market), *Good price* (7–12%), *Price dropped*, *Lowest we've seen*, or warnings like *Discount on paper only* and *Crossed-out price looks inflated*.
  - The market price and what you actually save.
  - The sale end date, when the site publishes one.

  By default only confirmed deals appear.
- **Browse.** Search and filter the whole catalogue, sorted by price or rating.
- **Device page.**
  - Every Nepali seller's price, including ones ignored as implausible (possible fakes).
  - Price history and full specs. Hover a spec to see which site it came from.
- **Compare.** Up to four devices side by side, with the best value in each row highlighted.
- **Data sources.**
  - *Check sources* tests every site from your connection.
  - *Update prices* scrapes them and shows a live log.
  - Each source shows its last result.

The site is also a PWA: in Chrome or Edge, "Install app" gives it its own window and icon.

## Command line

The same features work without the UI.

### First run (on a machine with internet)

```bash
devicescout sources          # list every configured source
devicescout check            # detect each store's platform, pull 3 sample products, report OK/FAIL
devicescout scrape --all     # fill the database (specs from GSMArena, prices from Nepali stores)
```

Every source in `sources.json` has `verified: false` because none has been run against the live site yet. `check` is how you find out which ones work. When a source fails, the output tells you what to change: `url_include`, `collections`, `fetch_mode`, or selectors.

### Get advice

```bash
devicescout advise -i        # answer a few questions

devicescout advise --category phone --budget 30k-60k --use photography:2,battery --os android --need 5g
devicescout advise --category laptop --budget "1.2 lakh" --use programming,student --min-ram 16
devicescout advise --category power_bank --budget 6000 --use fast_charging --min-capacity 20000
devicescout advise --category smartwatch --budget 25k --use fitness --need gps,water --official-only
```

Example output, produced from the test fixtures:

```
Best phones for photography x2 + battery, Rs 30,000-60,000, android
compared 2 devices (skipped: 1 no nepal price)

1. Samsung Galaxy A56 5G  |  Rs 52,999  |  score 83  |  confidence 71%
     + best main camera (50 MP) of the options
     + best OIS stabilisation (yes) of the options
     - heavier (198 g) than alternatives
     ! no seller confirmed as official/authorised: check warranty before paying
     > brother-mart: Rs 52,999 (8/128)  https://brother-mart.com/products/...
     > gadgetbyte: Rs 54,999 (listed price, not a shop)  https://www.gadgetbytenepal.com/...
```

**Uses:** photography, gaming, battery, longevity (lasts for years: promised OS upgrades, recent chip), portability, display, everyday, student, business, programming, content_creation, fitness, fast_charging, balanced. Add `:N` to set a use's priority, for example `photography:2,battery`.

**Must-haves:**
- `--need 5g,nfc,gps,gpu,ois,water`
- `--min-ram`, `--min-storage`, `--min-battery`, `--min-charging`, `--min-refresh`, `--max-weight`
- `--min-screen`, `--max-screen`, `--min-capacity`, `--min-output`

**Budget formats:** `50000`, `50k`, `30k-60k`, `1.5 lakh`, `40000-`

### Ask in plain words

```bash
devicescout ask "photography phone under 1.2 lakh"
devicescout ask "long lasting android phone around 60k with 5g, no samsung"
devicescout ask "gaming laptop between 1 lakh and 1.5 lakh with rtx"
devicescout ask "20000mah power bank 5 hajar samma"
```

The same box sits at the top of *Find a device* in the app. It understands:
- **The device type.**
- **Budgets**, written as `under`, `below`, `around`, `between ... and ...`, `50k`, `1.2 lakh`, `5 hajar`, `40k samma`, `1 lakh bhitra` or `1,20,000`.
- **Uses**, in the order you mention them: camera or photos, gaming or PUBG, battery, "long lasting" or "future proof", coding, student, fitness...
- **OS, brands to include or avoid** ("no samsung"), and **must-haves** (`5g`, `nfc`, `waterproof`, `16gb ram`, `5000mah`, `120hz`, `65w`, `rtx`).

It fills in the form so you can see and correct how it understood you. It's rule-based, so it works offline, costs nothing and gives the same result every time.

### Deals

```bash
devicescout deals                   # confirmed deals, biggest real saving first
devicescout deals --category phone --all   # include store claims we couldn't confirm
```

Deals come from normal scraping:
- Stores' own "was" prices (Daraz original price, Shopify compare-at price, WooCommerce regular price, schema.org list price).
- Sale end dates (`priceValidUntil`).
- Price cuts seen between scrapes.

Shopify stores' sale and festival collections (sale, offer, flash, Dashain, Tihar...) are found and crawled automatically.

### Search, data quality and reprocessing

```bash
devicescout search s24 ultra        # full-text: names, every alias a model was listed under, chipsets
devicescout quality                 # what cleaning rejected or fixed, and where sources disagree
devicescout reprocess               # rebuild the catalogue from stored raw records with current parsers
devicescout scrapers                # which fallback scrapers are ready (and why not)
devicescout scrapers --test URL     # fetch a page through each scraper separately
```

## Data layers

In Docker the data is kept in PostgreSQL, one schema per layer:

| Schema | What's in it |
|---|---|
| `raw` | Everything as it came off each website, kept forever. `raw.pages` holds every page and API response fetched (compressed, one row per version: an unchanged page only updates `last_seen_at`). `raw.records` holds every product record as parsed, before cleaning. Both are **partitioned by website** (`raw.pages_hukut`, `raw.records_gadgetbyte`, ...), so one site can be inspected, re-parsed or dropped on its own |
| `clean` | The merged, cleaned catalogue: `products` (one row per model, with every source's value for each spec), `offers` (every price ever seen, per seller and variant; `latest_offers` is the current one), `aliases`, `quality_issues`, `search_index` |
| `category` | One view per device type with typed columns and the current price range in Nepal: `category.phone` (ram_gb, main_camera_mp, has_5g, ...), `category.laptop` (has_dedicated_gpu, battery_wh, ...), `category.tv`, `category.appliance`, ... Always up to date with `clean` |
| `ops` | The job queue and small shared settings |

```sql
-- phones under Rs 60,000 with 5G and at least 8 GB RAM, cheapest first
SELECT name, lowest_price_npr, sellers, ram_gb, main_camera_mp, battery_mah
FROM category.phone
WHERE has_5g AND ram_gb >= 8 AND lowest_price_npr < 60000
ORDER BY lowest_price_npr;
```

Extensions: `pg_trgm` (typo-tolerant search: "galxy s24" finds the Galaxy S24), `unaccent`, `btree_gin`, and `pg_stat_statements` for query timings. Outside Docker the app uses a single SQLite file instead (no raw page archive); set `DEVICESCOUT_DB=postgresql://...` to use PostgreSQL anywhere.

## Scraping pipeline

Every record goes through the same four steps (`pipeline.py`):

0. **Crawl.** Whole sites, not just a few categories. With a product sitemap, every product in it. Without one (Hukut), every category, brand, offer and next page is walked, links on product pages are followed too, and listings that load as you scroll are scrolled to the end in the browser. `browse_pages` in `sources.json` caps the listing pages per run (default 400); `crawl_site: true` also walks sites that have a sitemap. Every page fetched is kept in `raw.pages`.
1. **Raw.** The record is stored exactly as parsed, and duplicates are skipped by content hash. When parsers improve, `reprocess` rebuilds everything without visiting the sites again.
2. **Clean.** Values that are impossible for the device type are dropped: a 20,000 mAh "phone" battery, a 1 kg phone, a Rs 1,999 "phone" that is really an EMI instalment, a "discount" from 10x the price. Junk names are rejected and HTML entities fixed. Every change is logged in `quality_issues`.
3. **Refine.** Each source's value for a spec is kept. The value shown comes from the most trusted source, then from agreement between sources. For example, two shops saying ~5,000 mAh beat one saying 4,000, and the disagreement is logged.
4. **Index.** A full-text index (PostgreSQL `tsvector` + trigram, or SQLite FTS5) covers each model's names, brand, chipset and every listing title it appeared under.
5. **Categorise.** Phones, laptops, tablets, watches, earbuds, power banks, chargers, cases, cables, accessories, speakers, TVs, monitors, cameras, consoles, networking, storage and home appliances, from the product name, the store's breadcrumb, and the category page it was listed on. Anything else is kept as "Other".

Pages are fetched through a **chain of scrapers** (`sources/backends.py`):

| Order | Scraper | Notes |
|---|---|---|
| 1 | Scrapling HTTP | curl_cffi with a real browser's network fingerprint |
| 2 | urllib | plain Python HTTP |
| 3 | Scrapling dynamic | Playwright |
| 4 | Scrapling stealth | Camoufox |
| 5 | Crawl4AI | optional |
| 6 | Firecrawl | optional; hosted, needs `FIRECRAWL_API_KEY` |

A response counts as blocked on 401/403/429/503, on a bot-wall page, or when HTML arrives where JSON was expected; the next scraper is then tried. The one that gets through is remembered for that site. A 404 is not retried. Install the optional scrapers with `pip install "devicescout[extra-scrapers]"`.

## Prices abroad and exchange rates

Every scrape starts by fetching today's exchange rates: Nepal Rastra Bank's official rates first, open.er-api.com if NRB is unreachable, the last saved rates if both are down. Any price a site shows in another currency (a USD price on a review site, GSMArena's "Price" row, an Indian store in INR) is converted to NPR with those rates.

Devices no Nepali store sells are still shown: in Browse, Compare, the product page and the advisor, as "≈ Rs 1,25,860, converted from $899 · Not sold in Nepal yet". Hovering the price shows the rate and its date. Converted prices leave out import duty, VAT and shipping, so a device bought here usually costs more. They are never mixed into real Nepali prices, deals or price history. In the advisor, "Only sold in Nepal" hides them. A device with no price anywhere is shown as "No price yet" when you haven't set a budget.

## How the advisor decides

1. **Hard filters.** Only offers from Nepal count, in NPR. Budget, OS, brands, must-haves, and the `--official-only` and `--in-stock` options are applied here. If a must-have *can't be checked* because the data is missing, the device isn't dropped. It's marked "could not confirm" and loses points, so a listing can't win just by leaving data out.
2. **Weighting.** The weights for each use are blended. A fixed 20% goes to "is it actually good": expert review score, buyer rating (only with at least 5 reviews), and model year.
3. **Scoring against affordable devices only.** Each spec is scored as a percentile among the devices you can afford. So "top-tier zoom for this budget" is literally true. **Confidence** is how much of that score rests on real data.
4. **Results.** You get the top picks, plus a **value pick** (at least 85% as good, costs less) and a **stretch pick** (up to 15% over budget, only when it's clearly better).
5. **Warnings.**
   - Offers that look fake: clone names like "i16 Pro Max", or a price far below other sellers of the same variant.
   - No authorised seller.
   - The Nepal price is well above the international reference price.

## Sources (`sources.json`)

| Region | Role | Sources |
|---|---|---|
| Nepal stores | prices, stock, sellers | Daraz NP, Hukut, Brother-mart, Oliz Store, Fatafat Sewa, itti, OnlineIT, iTechStore, SmartDoko, SastoDeal |
| Nepal reference | official/listed NPR prices + specs | Gadgetbyte Nepal, PriceNepal, NepalTechHub |
| International | specs | GSMArena |
| International | expert review scores | Notebookcheck (DXOMARK is listed but disabled: it needs a browser-rendered parser) |
| International | price sanity check | Samsung India (disabled until you've reviewed its terms). The NPR is pegged to the INR at 1.6. |

The adapters work at the platform level, so most sites need no custom code:
- `daraz`: the JSON behind Daraz's own catalog page (`?ajax=true`).
- `shopify`: `/products.json`.
- `woocommerce`: the public Store API `/wp-json/wc/store/v1`.
- `jsonld`: schema.org Product or Review data, with product pages found through the site's sitemap.
- `auto`: tries these in order and caches the result.

To add a store, add `{"name": ..., "type": "auto", "base_url": ...}` and run `devicescout check <name>`.

## Code map

| Path | What it does |
|---|---|
| `web/` | Vite + React + TypeScript frontend. `npm run dev` proxies `/api` to a running `devicescout serve`; `npm run build` writes to `devicescout/web/dist`. The build is committed, so a pip install from git works without Node |
| `devicescout/server.py` | FastAPI app: `/api/meta`, `/api/advise`, `/api/ask`, `/api/deals`, `/api/products`, `/api/sources`, `/api/quality`, `/api/health` and `/api/jobs`, plus the built UI. API docs are at `/api/docs` |
| `devicescout/jobs.py` | Check and scrape runs, shared by the CLI and the UI |
| `devicescout/paths.py` | Per-user data folder; finds files bundled with the package |
| `devicescout/sample.py` | The fictional demo catalogue behind `serve --sample` |
| `devicescout/specmeta.py` | Labels, units, use cases and must-have options the UI renders from |
| `sources/base.py` | `Fetcher`: a cookie-keeping Scrapling session with static, dynamic or stealth mode, per-host throttling and robots.txt checks |
| `sources/daraz.py`, `platforms.py`, `generic.py`, `gsmarena.py` | The site adapters described above |
| `sources/detect.py`, `registry.py` | Platform auto-detection, and building adapters from `sources.json` |
| `normalize.py` | Spec text to fixed fields. Also parses Nepali prices ("Rs. 1,49,999", "1.5 lakh") and storage variants ("8/256"), cleans marketplace titles, sorts devices into categories, and works out the OS from the model name |
| `currency.py` | Converts prices to NPR. The INR rate is fixed by the peg; other rates are approximate, so override them with `DEVICESCOUT_RATES='{"USD": 141.2}'` |
| `pricing.py` | Detects fake, used or mislisted offers |
| `pgstore.py` | PostgreSQL: the raw / clean / category layers, per-website partitions, search and the job queue |
| `storage.py` | SQLite (and the shared catalogue logic). One row per model, matched by barcode first and then by cleaned name. Price history is kept per seller and variant, and each spec records which source it came from |
| `scoring.py`, `advisor.py` | Use-case weights, percentile scoring, and the explained shortlist |
| `query.py` | Turns plain-language requests into needs |
| `deals.py` | Finds deals and checks them against the market price and price history |
| `pipeline.py`, `clean.py` | The raw → clean → refine → index pipeline, and reprocessing |
| `sources/backends.py` | The scraper fallback chain and bot-wall detection |
| `Dockerfile`, `docker-compose.yml` | The images (website, and scraper with the browsers), and the local web + scraper + PostgreSQL stack |

See [docs/scrapers.md](docs/scrapers.md) for the other scraping tools and when to use them.

## Known limitations

- **Nothing has run against a live site yet.** This build environment blocks those hosts. The tests use fixtures that match each platform's documented format, and the Daraz field names come from a working open-source client. Run `check` before you trust any source.
- **Store listings are thin on specs.** A Daraz-only device scores with low confidence until GSMArena or a spec-rich store provides its specs. Scrape GSMArena in the same run.
- **Specs aren't quality.** Expert scores (Notebookcheck, and DXOMARK once it's enabled) are what make the photography and gaming rankings trustworthy. `chip_tier` is a hand-made stand-in until benchmark data is added.
- **Browser scrapers are slow and heavy** (about 1 GB more image, and seconds per page). They only run when the fast HTTP scrapers are blocked or a page is built by JavaScript.
- **Terms of service.** Check each site's terms and robots.txt, and keep the default delays. Prefer an official feed or API wherever a store offers one.
