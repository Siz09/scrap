# DeviceScout

DeviceScout scrapes phones, smartwatches, tablets, laptops and accessories (power banks, chargers, cases, cables and earbuds) from several websites. It uses [Scrapling](https://github.com/D4Vinci/Scrapling) for fetching and parsing. It turns each site's spec text into one shared set of fields, merges listings of the same model from different sites, and ranks devices for a buyer profile such as photography, gaming, battery, portability, display or balanced. You can also filter by OS and budget.

```
fetch (Scrapling)  ->  parse (per-site adapter)  ->  normalize specs  ->  dedupe/merge (SQLite)  ->  rank by profile
```

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
scrapling install        # only needed for --mode dynamic / stealth (downloads browsers)
pytest
```

## Usage

```bash
# Spec database: phones, tablets and watches from GSMArena
devicescout scrape gsmarena --brand samsung --pages 2 --limit 40

# Any store, driven by a JSON config (see sites.example.json)
devicescout scrape site --config sites.json --site my-store --limit 50

# Debug a site offline: save the HTML page, then check what gets extracted
devicescout parse-file saved.html --url https://store/p/123 --source my-store

# Rank devices for a buyer
devicescout recommend --category phone --profile photography --os android --max-price 900
devicescout recommend --category power_bank --profile portability --sort value
devicescout recommend --category laptop --profile gaming --min-coverage 0.6 --json

# Export the catalogue
devicescout export --format csv --category laptop --out laptops.csv
```

## Design

| Module | What it does |
|---|---|
| `sources/base.py` | `Fetcher` wraps Scrapling's static, dynamic and stealth fetchers. It waits between requests to the same host and checks robots.txt. `Source` is the interface each site adapter implements. |
| `sources/gsmarena.py` | Parses GSMArena spec tables. The labels are keyed as `Section / Label`, for example `Battery / Charging`. |
| `sources/generic.py` | Works on most stores. It reads schema.org `Product` JSON-LD for the name, brand, price, currency, stock and rating. It also collects spec rows from `<table>`, `<dl>` or CSS selectors set per site. You add a new store by editing the config, not the code. |
| `normalize.py` | Converts spec text to fixed fields (`ram_gb`, `battery_mah`, `optical_zoom_x`, `output_w`, and so on). It also assigns categories and builds `canonical_key`, which removes storage, colour and carrier words so variants of one model become one record. |
| `storage.py` | Stores one row per model and many offers per model, so price history is kept. When sources disagree, the higher-priority source wins; GSMArena outranks store pages. It records which source each spec came from. |
| `scoring.py` | Each spec is scored as a percentile among the devices being compared, then weighted by the profile. `coverage` reports how much of the score rests on real data, and scores with missing data are pulled toward 50. |

## Known limitations

- **The site selectors have not been tested on live pages.** The tests use HTML I wrote by hand to match the structure of GSMArena and typical stores. Before you rely on a site, check it with `parse-file` against a real saved page.
- **Specs don't tell you how good a camera or a game experience is.** A 200 MP sensor doesn't make a better photo. For the photography and gaming profiles to mean much, you need review or benchmark data: DXOMARK-style camera scores, Geekbench/3DMark and Notebookcheck. `benchmark_score` is already in the schema and scoring, but nothing fills it yet. `chip_tier` is a rough stand-in until then.
- **Merging duplicates is the hardest part.** `canonical_key` works by removing known words. A new colour name or a regional model code can still produce duplicates. A proper fix would match on model numbers such as SM-S928 or GTIN/EAN codes, which many stores include in their JSON-LD.
- **Terms of service.** Many stores, Amazon in particular, don't allow scraping. Where an affiliate or product API exists (Amazon PA-API, Best Buy API, eBay Browse API), use it. It is more reliable and less legally risky.
