"""Command line entry point.

  devicescout scrape gsmarena --brand samsung --limit 20
  devicescout scrape site --config sites.example.json --site my-store --limit 50
  devicescout parse-file page.html --url https://... --source gsmarena
  devicescout recommend --category phone --profile photography --os android --max-price 900
  devicescout export --format csv --out devices.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys

from scrapling.parser import Selector

from .models import Category
from .scoring import PROFILES, rank
from .sources import Fetcher, GenericSource, GSMArenaSource, SiteConfig, load_site_configs
from .storage import Store


def _source(args) -> tuple:
    if args.source == "gsmarena":
        return GSMArenaSource(), {"brand": args.brand, "pages": args.pages}
    if args.source == "site":
        configs = load_site_configs(args.config)
        if args.site not in configs:
            sys.exit(f"site {args.site!r} not in {args.config}; have: {', '.join(configs)}")
        return GenericSource(configs[args.site]), {}
    sys.exit(f"unknown source {args.source}")


def cmd_scrape(args) -> None:
    source, opts = _source(args)
    fetcher = Fetcher(mode=args.mode or source.fetch_mode, delay=args.delay,
                      respect_robots=not args.ignore_robots)
    store = Store(args.db)
    n = 0
    for product in source.crawl(fetcher, limit=args.limit, **opts):
        key = store.upsert(product)
        n += 1
        print(f"[{product.category.value:>10}] {product.name}  ->  {key}")
    print(f"saved {n} products to {args.db}")


def cmd_parse_file(args) -> None:
    with open(args.file, encoding="utf-8") as f:
        page = Selector(f.read(), url=args.url)
    if args.source == "gsmarena":
        product = GSMArenaSource().parse(page)
    else:
        product = GenericSource(SiteConfig(name=args.source)).parse(page)
    if not product:
        sys.exit("no product found on page")
    if args.save:
        Store(args.db).upsert(product)
    print(json.dumps(product.to_dict(), indent=2, default=str))


def cmd_recommend(args) -> None:
    category = Category(args.category)
    products = Store(args.db).products(category)
    results = rank(products, args.profile, category, os=args.os, max_price=args.max_price,
                   min_coverage=args.min_coverage)
    if args.sort == "value":
        results = sorted(results, key=lambda r: r.value_score or 0, reverse=True)
    results = results[: args.top]
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
        return
    if not results:
        print("no matches (try loosening filters, or scrape more sources)")
        return
    print(f"{'#':>2}  {'score':>5}  {'cover':>5}  {'price':>9}  name")
    for i, r in enumerate(results, 1):
        price = f"{r.product.best_price:,.0f}" if r.product.best_price else "-"
        print(f"{i:>2}  {r.score:5.1f}  {r.coverage:5.0%}  {price:>9}  {r.product.name}")


def cmd_export(args) -> None:
    category = Category(args.category) if args.category else None
    rows = [p.to_dict() for p in Store(args.db).products(category)]
    out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    if args.format == "json":
        json.dump(rows, out, indent=2, default=str)
    else:
        spec_keys = sorted({k for r in rows for k in r["specs"]})
        w = csv.writer(out)
        w.writerow(["name", "brand", "category", "best_price", "rating", "source", *spec_keys])
        for r in rows:
            w.writerow([r["name"], r["brand"], r["category"], r["best_price"], r["rating"], r["source"],
                        *[r["specs"].get(k, "") for k in spec_keys]])
    if args.out:
        out.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="devicescout")
    ap.add_argument("--db", default="devicescout.db")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scrape", help="crawl a source and store products")
    s.add_argument("source", choices=["gsmarena", "site"])
    s.add_argument("--brand", default="samsung", help="gsmarena brand")
    s.add_argument("--pages", type=int, default=1)
    s.add_argument("--config", default="sites.example.json")
    s.add_argument("--site", help="site name in --config")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--delay", type=float, default=2.0, help="seconds between hits to one host")
    s.add_argument("--mode", choices=["static", "dynamic", "stealth"])
    s.add_argument("--ignore-robots", action="store_true")
    s.set_defaults(func=cmd_scrape)

    s = sub.add_parser("parse-file", help="parse a saved HTML page (debug selectors offline)")
    s.add_argument("file")
    s.add_argument("--url", required=True)
    s.add_argument("--source", default="generic")
    s.add_argument("--save", action="store_true")
    s.set_defaults(func=cmd_parse_file)

    s = sub.add_parser("recommend", help="rank stored devices for a buyer profile")
    s.add_argument("--category", required=True, choices=[c.value for c in Category])
    s.add_argument("--profile", default="balanced", choices=list(PROFILES))
    s.add_argument("--os")
    s.add_argument("--max-price", type=float)
    s.add_argument("--min-coverage", type=float, default=0.0)
    s.add_argument("--sort", choices=["score", "value"], default="score")
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_recommend)

    s = sub.add_parser("export", help="dump the catalogue as CSV or JSON")
    s.add_argument("--format", choices=["csv", "json"], default="csv")
    s.add_argument("--category", choices=[c.value for c in Category])
    s.add_argument("--out")
    s.set_defaults(func=cmd_export)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    args.func(args)


if __name__ == "__main__":
    main()
