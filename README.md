# DeviceScout

DeviceScout helps buyers in Nepal pick a phone, laptop, tablet, smartwatch or accessory (power banks, chargers, cases, cables, earbuds). You give it what you'll use the device for and your budget in NPR. It returns a short list of devices, explains why each one fits, and says where to buy it and at what price.

It scrapes Nepali stores for prices, including Daraz, Shopify and WooCommerce shops, and stores found by auto-detection. It uses Nepali tech sites for official listed prices, and trusted international sites for specs (GSMArena) and expert reviews (Notebookcheck). It merges these into one record per model. All scraping uses [Scrapling](https://github.com/D4Vinci/Scrapling).

```
sources.json ─► fetch (Scrapling) ─► adapter (Daraz JSON │ Shopify │ WooCommerce │ JSON-LD │ GSMArena)
            ─► normalize specs + NPR prices ─► merge by model / barcode (SQLite) ─► flag fake prices
            ─► advisor: budget + uses + must-haves ─► explained shortlist
```

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
scrapling install        # only needed for fetch_mode dynamic / stealth (downloads browsers)
pytest
```

## First run (on a machine with internet)

```bash
devicescout sources          # list every configured source
devicescout check            # detect each store's platform, pull 3 sample products, report OK/FAIL
devicescout scrape --all     # fill the database (specs from GSMArena, prices from Nepali stores)
```

Every source in `sources.json` has `verified: false` because none has been run against the live site yet. `check` is how you find out which ones work. When a source fails, the output tells you what to change: `url_include`, `collections`, `fetch_mode`, or selectors.

## Get advice

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

**Uses:** photography, gaming, battery, portability, display, everyday, student, business, programming, content_creation, fitness, fast_charging, balanced. Add `:N` to set a use's priority, for example `photography:2,battery`.

**Must-haves:**
- `--need 5g,nfc,gps,gpu,ois,water`
- `--min-ram`, `--min-storage`, `--min-battery`, `--min-charging`, `--min-refresh`, `--max-weight`
- `--min-screen`, `--max-screen`, `--min-capacity`, `--min-output`

**Budget formats:** `50000`, `50k`, `30k-60k`, `1.5 lakh`, `40000-`

### How the advisor decides

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

| Module | What it does |
|---|---|
| `sources/base.py` | `Fetcher`: a cookie-keeping Scrapling session with static, dynamic or stealth mode, per-host throttling and robots.txt checks |
| `sources/daraz.py`, `platforms.py`, `generic.py`, `gsmarena.py` | The site adapters described above |
| `sources/detect.py`, `registry.py` | Platform auto-detection, and building adapters from `sources.json` |
| `normalize.py` | Spec text to fixed fields. Also parses Nepali prices ("Rs. 1,49,999", "1.5 lakh") and storage variants ("8/256"), cleans marketplace titles, sorts devices into categories, and works out the OS from the model name |
| `currency.py` | Converts prices to NPR. The INR rate is fixed by the peg; other rates are approximate, so override them with `DEVICESCOUT_RATES='{"USD": 141.2}'` |
| `pricing.py` | Detects fake, used or mislisted offers |
| `storage.py` | SQLite. One row per model, matched by barcode first and then by cleaned name. Price history is kept per seller and variant, and each spec records which source it came from |
| `scoring.py`, `advisor.py` | Use-case weights, percentile scoring, and the explained shortlist |

See [docs/scrapers.md](docs/scrapers.md) for the other scraping tools and when to use them.

## Known limitations

- **Nothing has run against a live site yet.** This build environment blocks those hosts. The tests use fixtures that match each platform's documented format, and the Daraz field names come from a working open-source client. Run `check` before you trust any source.
- **Store listings are thin on specs.** A Daraz-only device scores with low confidence until GSMArena or a spec-rich store provides its specs. Scrape GSMArena in the same run.
- **Specs aren't quality.** Expert scores (Notebookcheck, and DXOMARK once it's enabled) are what make the photography and gaming rankings trustworthy. `chip_tier` is a hand-made stand-in until benchmark data is added.
- **Terms of service.** Check each site's terms and robots.txt, and keep the default delays. Prefer an official feed or API wherever a store offers one.
