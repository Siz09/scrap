# Scraping tools: what else exists, and when to use it

Scrapling stays the core tool. Its fetcher uses curl_cffi to imitate a real browser's network fingerprint, and it can also drive Playwright or Camoufox browsers. Its selectors can relocate elements after a site changes its HTML. Version 0.4 also includes a Scrapy-style **spider framework** (`scrapling.spiders`) with a scheduler, per-domain throttling, robots.txt handling, pause/resume checkpoints, and ready-made `SitemapSpider` and `ShopifySpider` templates. For this project, that covers most of what Scrapy would add. The other tools below are worth adding only for the specific jobs listed.

Version numbers are from PyPI as of September 2026.

| Tool | What it is | Use it here for | Skip it because |
|---|---|---|---|
| **Scrapling 0.4** (current) | Fetchers (curl_cffi, Playwright, Camoufox), adaptive selectors, spider framework | Everything today | n/a |
| **Scrapy 2.19** | The long-established crawl framework: pipelines, middlewares, many plugins | Only if you need its plugin ecosystem or scrapyd deployment | Scrapling's spiders now cover the core; two frameworks means two ways to do everything |
| **Crawlee (Python) 1.10** | Apify's framework; one API for HTTP and browser crawling, with queues and sessions | A large JS-heavy crawl that needs managed browser pools | Overlaps Scrapling; adds a heavier runtime |
| **Crawl4AI 0.9** | Crawler that outputs LLM-ready Markdown and does LLM-based extraction | Pulling specs out of messy **Nepali product descriptions** that no regex handles, as a fallback | Needs an LLM call per page, so it's slow and costs money at catalogue scale |
| **ScrapeGraphAI 1.76** | Describe the data in plain English and an LLM builds the extraction | Quick prototypes for a new site | Output isn't deterministic, and it costs money per page |
| **Firecrawl** (`firecrawl-py` 4.44) | Hosted (or self-hosted) crawl-to-Markdown/JSON API | Offloading anti-bot problems for a few hard sites | A paid service, and your data flows through a third party |
| **Botasaurus 4.0** / **nodriver 0.50** | Undetected Chrome automation | Sites where Camoufox (Scrapling's stealth fetcher) still gets blocked | Try `fetch_mode: "stealth"` first |
| **curl_cffi 0.16** | Low-level HTTP client that imitates browser TLS fingerprints | n/a: Scrapling already uses it | Already included |
| **selectolax 0.4** | Very fast HTML parser | Bulk re-parsing of saved pages | Scrapling's parser is fast enough at this scale |
| **extruct 0.18** | Pulls JSON-LD, microdata and OpenGraph out of HTML | Sites that use microdata instead of JSON-LD | `sources/generic.py` already handles JSON-LD, which covers most sites |

## Daraz-specific prior art

- [MaheshPhuyal02/daraz-np-mcp](https://github.com/MaheshPhuyal02/daraz-np-mcp) (MIT): a Daraz Nepal client using the public `?ajax=true` catalog JSON. `sources/daraz.py` reads the same fields (`mods.listItems`, `price`, `ratingScore`, `sellerName`, ...).
- [sushil-rgb/Daraz-Global-WebScraper](https://github.com/sushil-rgb/Daraz-Global-WebScraper): an HTML scraper for Daraz in Nepal, Sri Lanka, Pakistan and Bangladesh.
- [yubint/darazscrape-api](https://github.com/yubint/darazscrape-api): a Django + Celery price tracker for daraz.com.np product pages.
- Several paid Apify actors scrape Daraz, for example "Daraz NP Product Scraper". They're useful if Daraz blocks your IP and you'd rather pay than maintain a workaround.

## Recommendation

1. Keep Scrapling. If crawls grow past a few thousand pages per site, move the long-running ones onto `scrapling.spiders` for concurrency and pause/resume. `ShopifySpider` and `SitemapSpider` line up with `sources/platforms.py` and `sources/generic.py`.
2. Add **Crawl4AI** as an optional fallback extractor, used only when a product's spec coverage stays below about 30% after every structured source has run. That limits the LLM cost to the pages that need it.
3. Don't add Scrapy, Crawlee or Selenium-style tools unless a specific site forces it.
