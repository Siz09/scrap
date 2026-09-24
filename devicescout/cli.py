"""Command line entry point.

  devicescout sources                       # what we scrape and its status
  devicescout check                         # detect platforms + sample each source (run this first)
  devicescout scrape --all                  # scrape every enabled source
  devicescout scrape daraz-np gsmarena --limit 100
  devicescout advise -i                     # answer a few questions, get a shortlist
  devicescout advise --category phone --budget 30k-60k --use photography:2,battery --os android --need 5g
  devicescout parse-file page.html --url https://... --source gsmarena
  devicescout export --format csv --out devices.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys

from scrapling.parser import Selector

from .advisor import Advice, Needs, Pick, advise
from .models import Category
from .scoring import PROFILES
from .sources import Fetcher, GenericSource, GSMArenaSource, SiteConfig, build, load_entries
from .sources.detect import cached_platform, detect, remember
from .storage import Store

MUST_FLAGS = {  # cli flag -> (spec key, op, cast)
    "min_ram": ("ram_gb", ">=", float), "min_storage": ("storage_gb", ">=", float),
    "min_battery": ("battery_mah", ">=", float), "min_charging": ("charging_w", ">=", float),
    "min_refresh": ("refresh_rate_hz", ">=", float), "max_weight": ("weight_g", "<=", float),
    "min_screen": ("display_size_in", ">=", float), "max_screen": ("display_size_in", "<=", float),
    "min_capacity": ("capacity_mah", ">=", float), "min_output": ("output_w", ">=", float),
}
NEED_FLAGS = {"5g": "has_5g", "nfc": "has_nfc", "gps": "has_gps", "gpu": "has_dedicated_gpu", "ois": "has_ois"}


def _entries(args) -> list[dict]:
    entries = load_entries(args.sources)
    if getattr(args, "names", None):
        wanted = set(args.names)
        missing = wanted - {e["name"] for e in entries}
        if missing:
            sys.exit(f"unknown source(s): {', '.join(sorted(missing))}")
        return [e for e in entries if e["name"] in wanted]
    return [e for e in entries if e.get("enabled", True)]


def cmd_sources(args) -> None:
    print(f"{'name':<15} {'on':<3} {'role':<10} {'region':<7} {'type':<12} {'verified':<8} notes")
    for e in load_entries(args.sources):
        kind = e.get("type", "auto")
        if kind == "auto":
            kind = f"auto:{cached_platform(e['name']) or '?'}"
        print(f"{e['name']:<15} {'y' if e.get('enabled', True) else 'n':<3} {e.get('role', ''):<10} "
              f"{e.get('region', ''):<7} {kind:<12} {str(e.get('verified', False)).lower():<8} "
              f"{(e.get('notes') or '')[:70]}")


def _crawl(entry: dict, fetcher: Fetcher, limit: int):
    source = build(entry, fetcher)
    if entry.get("type") == "gsmarena":
        per_brand = max(1, limit // max(1, len(entry.get("brands", ["samsung"]))))
        for brand in entry.get("brands", ["samsung"]):
            yield from source.crawl(fetcher, limit=per_brand, brand=brand, pages=entry.get("pages", 1))
    else:
        yield from source.crawl(fetcher, limit=limit)


def cmd_check(args) -> None:
    """Detect platform and pull a few products from each source; report what works."""
    rows = []
    with Fetcher(delay=args.delay) as fetcher:
        for e in _entries(args):
            status, detail = "FAIL", ""
            try:
                if e.get("type", "auto") == "auto":
                    report = detect(fetcher, e["base_url"])
                    remember(e["name"], report)
                    detail = f"{report['platform']}: {report['evidence']}"
                sample = []
                for p in _crawl(e, fetcher, limit=args.sample):
                    sample.append(p)
                    if len(sample) >= args.sample:
                        break
                if sample:
                    priced = sum(1 for p in sample if p.offers and p.offers[0].price)
                    specd = sum(1 for p in sample if p.specs)
                    status = "OK"
                    detail += f" | {len(sample)} products, {priced} priced, {specd} with specs; e.g. {sample[0].name[:40]!r}"
                else:
                    detail += " | no products extracted"
            except Exception as ex:
                detail += f" | {type(ex).__name__}: {str(ex)[:120]}"
            rows.append((e["name"], status, detail))
            print(f"{e['name']:<15} {status:<5} {detail}", flush=True)
    ok = sum(1 for r in rows if r[1] == "OK")
    print(f"\n{ok}/{len(rows)} sources working. Fix FAILs in {args.sources} (selectors, url_include, fetch_mode).")


def cmd_scrape(args) -> None:
    if not args.names and not args.all:
        sys.exit("name one or more sources, or pass --all (see `devicescout sources`)")
    store = Store(args.db)
    total = 0
    with Fetcher(mode=args.mode or "static", delay=args.delay,
                 respect_robots=not args.ignore_robots) as fetcher:
        for e in _entries(args):
            n = 0
            try:
                for product in _crawl(e, fetcher, args.limit):
                    store.upsert(product)
                    n += 1
                    if args.verbose:
                        print(f"  [{product.category.value:>10}] {product.name}")
            except Exception as ex:
                print(f"{e['name']}: stopped after {n} products ({type(ex).__name__}: {ex})")
                continue
            total += n
            print(f"{e['name']}: {n} products")
    print(f"saved {total} products to {args.db}")


def cmd_parse_file(args) -> None:
    with open(args.file, encoding="utf-8") as f:
        page = Selector(f.read(), url=args.url)
    if args.source == "gsmarena":
        product = GSMArenaSource().parse(page)
    else:
        product = GenericSource(SiteConfig(name=args.source, price_from_text=True)).parse(page)
    if not product:
        sys.exit("no product found on page")
    if args.save:
        Store(args.db).upsert(product)
    print(json.dumps(product.to_dict(), indent=2, default=str))


# --- advise -----------------------------------------------------------------

def _parse_uses(text: str) -> dict[str, float]:
    uses = {}
    for part in filter(None, (x.strip() for x in text.split(","))):
        name, _, prio = part.partition(":")
        uses[name.strip()] = float(prio) if prio else 1.0
    return uses


def _needs_from_args(args) -> Needs:
    lo, hi = Needs.parse_budget(args.budget) if args.budget else (None, None)
    must = {}
    for flag, (key, op, cast) in MUST_FLAGS.items():
        v = getattr(args, flag)
        if v is not None:
            must[key] = (op, cast(v))
    for n in filter(None, (x.strip().lower() for x in (args.need or "").split(","))):
        if n == "water":
            must["water_rating"] = (">=", 7)  # IP67 / 5ATM or better
        elif n in NEED_FLAGS:
            must[NEED_FLAGS[n]] = ("==", True)
        else:
            sys.exit(f"unknown --need {n!r}; choose from {', '.join([*NEED_FLAGS, 'water'])}")
    return Needs(
        category=Category(args.category), budget_min=lo, budget_max=hi,
        uses=_parse_uses(args.use or "balanced"),
        os=[o.strip() for o in (args.os or "").split(",") if o.strip()],
        brands=[b.strip() for b in (args.brand or "").split(",") if b.strip()],
        exclude_brands=[b.strip() for b in (args.exclude_brand or "").split(",") if b.strip()],
        must=must, official_only=args.official_only, in_stock_only=args.in_stock, top=args.top,
    )


_USES_BY_CATEGORY = {
    c: [u for u, per_cat in PROFILES.items() if c in per_cat] for c in Category
}


def _ask(prompt: str, default: str = "") -> str:
    ans = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return ans or default


def _needs_interactive() -> Needs:
    cats = [c.value for c in Category if _USES_BY_CATEGORY[c]]
    print("What are you buying? " + ", ".join(cats))
    category = Category(_ask("category", "phone"))
    lo, hi = Needs.parse_budget(_ask("Budget in NPR (e.g. 40000, 30k-60k, 1.5 lakh)", "50k"))
    uses = _USES_BY_CATEGORY[category]
    print("What will you mainly use it for? Pick one or more, most important first:")
    for i, u in enumerate(uses, 1):
        print(f"  {i}. {u.replace('_', ' ')}")
    picked = [uses[int(x) - 1] for x in _ask("numbers, comma separated", "1").split(",") if x.strip().isdigit()
              and 0 < int(x) <= len(uses)]
    # Earlier choices matter more: 3, 2, 1...
    weighted = {u: float(len(picked) - i) for i, u in enumerate(picked)} or {"balanced": 1.0}
    os_pref = _ask("Preferred OS (android/ios/windows/macos, blank = any)")
    must: dict = {}
    if category == Category.PHONE:
        if _ask("Need 5G? (y/n)", "n").lower().startswith("y"):
            must["has_5g"] = ("==", True)
        if (b := _ask("Minimum battery mAh (blank = any)")):
            must["battery_mah"] = (">=", float(b))
    if category == Category.LAPTOP and (r := _ask("Minimum RAM GB (blank = any)")):
        must["ram_gb"] = (">=", float(r))
    official = _ask("Only official/authorised sellers? (y/n)", "n").lower().startswith("y")
    return Needs(category=category, budget_min=lo, budget_max=hi, uses=weighted,
                 os=[os_pref] if os_pref else [], must=must, official_only=official)


def _print_pick(i: str, pick: Pick) -> None:
    r = pick.ranked
    price = f"Rs {pick.price:,.0f}" if pick.price else "price n/a"
    print(f"{i} {r.product.name}  |  {price}  |  score {r.score:.0f}  |  confidence {r.coverage:.0%}")
    for s in pick.strengths:
        print(f"     + {s}")
    for w in pick.weaknesses:
        print(f"     - {w}")
    for w in pick.warnings:
        print(f"     ! {w}")
    for o in pick.where_to_buy:
        tags = [t for t in (o["variant"], "official" if o["official"] else None,
                            "out of stock" if o["in_stock"] is False else None,
                            "listed price, not a shop" if o["listed_price_only"] else None) if t]
        print(f"     > {o['seller']}: Rs {o['price_npr']:,.0f}{' (' + ', '.join(tags) + ')' if tags else ''}  {o['url']}")


def _print_advice(a: Advice) -> None:
    n = a.needs
    budget = (f"Rs {n.budget_min:,.0f}-{n.budget_max:,.0f}" if n.budget_min and n.budget_max
              else f"up to Rs {n.budget_max:,.0f}" if n.budget_max else "any budget")
    uses = " + ".join(f"{u.replace('_', ' ')}" + (f" x{p:g}" if p != 1 else "") for u, p in n.uses.items())
    print(f"\nBest {n.category.value.replace('_', ' ')}s for {uses}, {budget}"
          + (f", {'/'.join(n.os)}" if n.os else ""))
    skipped = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in a.excluded.items() if v and k != "wrong_category")
    print(f"compared {a.considered} devices" + (f" (skipped: {skipped})" if skipped else "") + "\n")
    if not a.picks:
        print("Nothing matches. Try a higher budget, fewer must-haves, or scrape more sources.")
    for i, p in enumerate(a.picks, 1):
        _print_pick(f"{i}.", p)
        print()
    if a.value_pick:
        print("VALUE PICK: nearly as good, costs less")
        _print_pick("  ", a.value_pick)
        print()
    if a.stretch_pick:
        extra = a.stretch_pick.price - n.budget_max if n.budget_max else 0
        print(f"WORTH STRETCHING? Rs {extra:,.0f} over budget, clearly better")
        _print_pick("  ", a.stretch_pick)


def cmd_advise(args) -> None:
    needs = _needs_interactive() if args.interactive else _needs_from_args(args)
    advice = advise(Store(args.db).products(needs.category), needs)
    if args.json:
        print(json.dumps(advice.to_dict(), indent=2, default=str))
    else:
        _print_advice(advice)


def cmd_export(args) -> None:
    category = Category(args.category) if args.category else None
    rows = [p.to_dict() for p in Store(args.db).products(category)]
    out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    if args.format == "json":
        json.dump(rows, out, indent=2, default=str)
    else:
        spec_keys = sorted({k for r in rows for k in r["specs"]})
        w = csv.writer(out)
        w.writerow(["name", "brand", "category", "best_price_npr", "reference_price_npr", "rating", "source", *spec_keys])
        for r in rows:
            w.writerow([r["name"], r["brand"], r["category"], r["best_price_npr"], r["reference_price_npr"],
                        r["rating"], r["source"], *[r["specs"].get(k, "") for k in spec_keys]])
    if args.out:
        out.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="devicescout")
    ap.add_argument("--db", default="devicescout.db")
    ap.add_argument("--sources", default="sources.json", help="source registry file")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sources", help="list configured sources")
    s.set_defaults(func=cmd_sources)

    s = sub.add_parser("check", help="detect platforms and test each source with a small sample")
    s.add_argument("names", nargs="*")
    s.add_argument("--sample", type=int, default=3)
    s.add_argument("--delay", type=float, default=1.5)
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("scrape", help="scrape sources into the database")
    s.add_argument("names", nargs="*")
    s.add_argument("--all", action="store_true", help="every enabled source")
    s.add_argument("--limit", type=int, default=300, help="max products per source")
    s.add_argument("--delay", type=float, default=2.0, help="seconds between hits to one host")
    s.add_argument("--mode", choices=["static", "dynamic", "stealth"])
    s.add_argument("--ignore-robots", action="store_true")
    s.set_defaults(func=cmd_scrape)

    s = sub.add_parser("parse-file", help="parse a saved HTML page (debug a source offline)")
    s.add_argument("file")
    s.add_argument("--url", required=True)
    s.add_argument("--source", default="generic")
    s.add_argument("--save", action="store_true")
    s.set_defaults(func=cmd_parse_file)

    s = sub.add_parser("advise", help="shortlist devices for a buyer's needs and budget")
    s.add_argument("-i", "--interactive", action="store_true", help="ask questions instead of flags")
    s.add_argument("--category", default="phone", choices=[c.value for c in Category])
    s.add_argument("--budget", help="NPR: 50000, 50k, 30k-60k, 1.5 lakh")
    s.add_argument("--use", help=f"comma list, optional priority: photography:2,battery. One of: {', '.join(PROFILES)}")
    s.add_argument("--os", help="android,ios,windows,macos,...")
    s.add_argument("--brand", help="only these brands")
    s.add_argument("--exclude-brand")
    s.add_argument("--need", help="must-have features: 5g,nfc,gps,gpu,ois,water")
    for flag in MUST_FLAGS:
        s.add_argument(f"--{flag.replace('_', '-')}", dest=flag, type=float)
    s.add_argument("--official-only", action="store_true", help="only authorised sellers / Daraz Mall")
    s.add_argument("--in-stock", action="store_true")
    s.add_argument("--top", type=int, default=5)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_advise)

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
