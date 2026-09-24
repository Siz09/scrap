# DeviceScout

DeviceScout helps buyers in Nepal pick a phone, laptop, tablet, smartwatch or accessory (power banks, chargers, cases, cables, earbuds). You give it what you'll use the device for and your budget in NPR. It returns a short list of devices, explains why each one fits, and says where to buy it and at what price.

It scrapes Nepali stores for prices, including Daraz, Shopify and WooCommerce shops, and stores found by auto-detection. It uses Nepali tech sites for official listed prices, and trusted international sites for specs (GSMArena) and expert reviews (Notebookcheck). It merges these into one record per model. All scraping uses [Scrapling](https://github.com/D4Vinci/Scrapling).

```
sources.json ─► fetch (Scrapling) ─► adapter (Daraz JSON │ Shopify │ WooCommerce │ JSON-LD │ GSMArena)
            ─► normalize specs + NPR prices ─► merge by model / barcode (SQLite) ─► flag fake prices
            ─► advisor: budget + uses + must-haves ─► explained shortlist
```

## Install

DeviceScout runs on your computer as a local web app: a small server plus a browser UI. There are three ways to install it.

**1. Desktop app (no Python needed).** Download `DeviceScout-windows-x64.exe`, `DeviceScout-macos-arm64` or `DeviceScout-linux-x64` from the GitHub Releases page, then double-click it. A console window shows the address and your browser opens the app. Close the window to stop it. The builds are unsigned, so Windows SmartScreen and macOS Gatekeeper will warn the first time: choose "Run anyway" on Windows, or right-click then Open on macOS.

**2. pip.** You need Python 3.10 or newer.

```bash
pip install "git+https://github.com/Siz09/scrap.git"
devicescout serve            # opens http://127.0.0.1:8765
devicescout serve --sample   # try it with a fictional demo catalogue first
```

**3. From source,** for development:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
(cd web && npm ci && npm run build)   # builds the UI into devicescout/web/dist
pytest
devicescout serve
```

Your database, your editable copy of `sources.json`, and caches are stored in a per-user folder, never next to the program:

| System | Folder |
|---|---|
| Windows | `%LOCALAPPDATA%\DeviceScout` |
| macOS | `~/Library/Application Support/DeviceScout` |
| Linux | `~/.local/share/devicescout` |

Set `DEVICESCOUT_HOME` to use a different folder.

## Using the app

- **Find a device.**
  1. Pick a category and a budget in NPR (`45000`, `45k` and `1.5 lakh` all work).
  2. Tap what matters in order of importance: photography, gaming, battery, student, and so on.
  3. Add any must-haves (5G, NFC, minimum RAM, maximum weight...) and an OS.

  Results update as you change the inputs. Each device card shows a fit score, a confidence level, the reasons for its placing, and where to buy it. You'll also see a *Save money* pick and a *Worth stretching?* pick when one of them applies.
- **Browse.** Search and filter the whole catalogue, sorted by price or rating.
- **Device page.**
  - Every Nepali seller's price, including ones ignored as implausible (possible fakes).
  - Price history and full specs. Hover a spec to see which site it came from.
- **Compare.** Up to four devices side by side, with the best value in each row highlighted.
- **Data sources.**
  - *Check sources* tests every site from your connection.
  - *Update prices* scrapes them and shows a live log.
  - Each source shows its last result.

The app is also a PWA (installable web app): in Chrome or Edge, use "Install app" to get its own window and icon. On Android, use "Add to Home screen".

## Hosting it for buyers

Buyers shouldn't need to install anything. Run the scraper on your own machine or server, and serve the same app read-only:

```bash
devicescout scrape --all                                        # e.g. nightly, from cron / Task Scheduler
devicescout serve --host 0.0.0.0 --port 8765 --read-only --no-browser
```

`--read-only` removes the scraping endpoints, so visitors can only read. Put it behind a reverse proxy with HTTPS, such as Caddy or nginx. The PWA install prompt only appears over HTTPS.

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

### Search, data quality and reprocessing

```bash
devicescout search s24 ultra        # full-text: names, every alias a model was listed under, chipsets
devicescout quality                 # what cleaning rejected or fixed, and where sources disagree
devicescout reprocess               # rebuild the catalogue from stored raw records with current parsers
devicescout scrapers                # which fallback scrapers are installed
```

## Scraping pipeline

Every record goes through the same four steps (`pipeline.py`):

1. **Raw.** The record is stored exactly as parsed, and duplicates are skipped by content hash. When parsers improve, `reprocess` rebuilds everything without visiting the sites again.
2. **Clean.** Values that are impossible for the device type are dropped: a 20,000 mAh "phone" battery, a 1 kg phone, a Rs 1,999 "phone" that is really an EMI instalment, a "discount" from 10x the price. Junk names are rejected and HTML entities fixed. Every change is logged in `quality_issues`.
3. **Refine.** Each source's value for a spec is kept. The value shown comes from the most trusted source, then from agreement between sources. For example, two shops saying ~5,000 mAh beat one saying 4,000, and the disagreement is logged.
4. **Index.** An SQLite FTS5 full-text index covers each model's names, brand, chipset and every listing title it appeared under.

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
| `devicescout/server.py` | FastAPI app: `/api/meta`, `/api/advise`, `/api/products`, `/api/sources` and `/api/jobs` (background check/scrape), plus the built UI. API docs are at `/api/docs` |
| `devicescout/app.py` | Double-click entry point: picks a free port and opens the browser |
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
| `storage.py` | SQLite. One row per model, matched by barcode first and then by cleaned name. Price history is kept per seller and variant, and each spec records which source it came from |
| `scoring.py`, `advisor.py` | Use-case weights, percentile scoring, and the explained shortlist |
| `query.py` | Turns plain-language requests into needs |
| `pipeline.py`, `clean.py` | The raw → clean → refine → index pipeline, and reprocessing |
| `sources/backends.py` | The scraper fallback chain and bot-wall detection |
| `packaging/devicescout.spec` | PyInstaller one-file build, run with `pyinstaller packaging/devicescout.spec` |
| `.github/workflows/` | `ci.yml` runs the tests and checks the committed UI build. `release.yml` builds Windows, macOS and Linux executables plus the wheel, and publishes them on `v*` tags |

See [docs/scrapers.md](docs/scrapers.md) for the other scraping tools and when to use them.

## Known limitations

- **Nothing has run against a live site yet.** This build environment blocks those hosts. The tests use fixtures that match each platform's documented format, and the Daraz field names come from a working open-source client. Run `check` before you trust any source.
- **Store listings are thin on specs.** A Daraz-only device scores with low confidence until GSMArena or a spec-rich store provides its specs. Scrape GSMArena in the same run.
- **Specs aren't quality.** Expert scores (Notebookcheck, and DXOMARK once it's enabled) are what make the photography and gaming rankings trustworthy. `chip_tier` is a hand-made stand-in until benchmark data is added.
- **The executable only does plain HTTP scraping.** It leaves out the browser engines to stay small (about 40 MB). Sites that need `fetch_mode: dynamic` or `stealth` need the pip install plus `scrapling install`.
- **Terms of service.** Check each site's terms and robots.txt, and keep the default delays. Prefer an official feed or API wherever a store offers one.
