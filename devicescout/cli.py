"""Command line entry point.

  devicescout sources                       # what we scrape and its status
  devicescout check                         # detect platforms + sample each source (run this first)
  devicescout scrape --all                  # scrape every enabled source
  devicescout scrape daraz-np gsmarena --limit 100
  devicescout ask "photography phone under 1.2 lakh"
  devicescout advise -i                     # answer a few questions, get a shortlist
  devicescout advise --category phone --budget 30k-60k --use photography:2,battery --os android --need 5g
  devicescout parse-file page.html --url https://... --source gsmarena
  devicescout export --format csv --out devices.csv
  devicescout serve                         # the web app
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys

from scrapling.parser import Selector

from .advisor import Advice, Needs, Pick, advise
from .models import Category
from .scoring import PROFILES
from .jobs import run_check, run_scrape
from .paths import default_db, sources_path
from .sources import Fetcher, GenericSource, GSMArenaSource, SiteConfig, load_entries
from .sources.detect import cached_platform
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


def cmd_check(args) -> None:
    """Detect platform and pull a few products from each source; report what works."""
    rows = run_check(_entries(args), sample=args.sample, delay=args.delay, fetcher_factory=Fetcher)
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("OK", "PARTIAL", "FAIL")}
    print(f"\n{counts['OK']} working, {counts['PARTIAL']} partial, {counts['FAIL']} failing of {len(rows)}. "
          f"Fix sources in {args.sources} (start_urls, url_include, fetch_mode).")


def cmd_scrape(args) -> None:
    if not args.names and not args.all:
        sys.exit("name one or more sources, or pass --all (see `devicescout sources`)")
    counts = run_scrape(_entries(args), args.db, limit=args.limit, delay=args.delay, mode=args.mode or "static",
                        respect_robots=not args.ignore_robots, fetcher_factory=Fetcher, verbose=args.verbose)
    print(f"saved {sum(counts.values())} products to {args.db}")


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
        from .pipeline import ingest
        ingest(Store(args.db), product)
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
    print(f"compared {a.considered} device{'s' if a.considered != 1 else ''}" + (f" (skipped: {skipped})" if skipped else "") + "\n")
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


def cmd_serve(args) -> None:
    from .server import serve
    serve(host=args.host, port=args.port, db=args.db, sources=args.sources, open_browser=not args.no_browser,
          read_only=args.read_only, sample=args.sample)


def cmd_ask(args) -> None:
    from .advisor import needs_from_query
    from .query import parse_query

    parsed = parse_query(" ".join(args.text))
    print("Understood: " + (" | ".join(parsed.understood) or "nothing specific (showing all-rounders)"))
    needs = needs_from_query(parsed, top=args.top)
    advice = advise(Store(args.db).products(needs.category), needs)
    if args.json:
        print(json.dumps({"parsed": parsed.to_dict(), "advice": advice.to_dict()}, indent=2, default=str))
    else:
        _print_advice(advice)


def _duration(text: str) -> float:
    """'6h', '90m', '1d', '3600' -> seconds."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", text.lower())
    if not m:
        raise argparse.ArgumentTypeError(f"not a duration: {text!r} (use e.g. 30m, 6h, 1d)")
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def cmd_schedule(args) -> None:
    """The scraper service: scheduled scrapes plus any jobs queued from the website."""
    import random
    import signal
    import threading
    import time
    from datetime import datetime, timezone

    from .jobs import execute

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    store = Store(args.db)
    store.fail_stale_jobs()

    def say(line: str) -> None:
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}", flush=True)

    def heartbeat() -> None:
        store.set_kv("worker_heartbeat", datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def run(kind: str, origin: str) -> None:
        # Scheduled runs go through the same queue, so the website shows their progress too.
        store.enqueue_job(kind, [], args.limit, origin=origin)
        drain()

    def drain() -> None:
        while not stop.is_set() and (job := store.claim_job()):
            say(f"{job['kind']} job {job['id']} ({job['origin']}) started")
            status = execute(job, args.db, args.sources, fetcher_factory=Fetcher)
            for line in (store.job(job["id"]) or {}).get("log", [])[-3:]:
                say("  " + line)
            say(f"{job['kind']} job {job['id']} {status}")
            heartbeat()

    from .sources.backends import describe
    store.set_kv("worker_scrapers", json.dumps(describe()))   # what *this* container can run, for the website
    heartbeat()
    if args.check_first:
        run("check", "schedule")
    while not stop.is_set():
        run("scrape", "schedule")
        if args.once:
            break
        next_run = time.monotonic() + args.every + random.uniform(0, args.jitter)
        say(f"next scheduled scrape in {(next_run - time.monotonic()) / 3600:.1f} h; watching for jobs from the website")
        while not stop.is_set() and time.monotonic() < next_run:
            heartbeat()
            drain()
            stop.wait(5)
    say("scheduler stopped")


def cmd_deals(args) -> None:
    from .deals import find_deals
    category = Category(args.category) if args.category else None
    deals = find_deals(Store(args.db), category, verified_only=not args.all)
    if not deals:
        print("No deals right now." + ("" if args.all else " (--all also shows unverified store claims)"))
    for d in deals[: args.limit]:
        o = d.offer
        was = f" (was Rs {o.original_price:,.0f}, claims {d.claimed_pct:g}% off)" if d.claimed_pct else ""
        if d.saving_pct is None:
            real = "no market price to compare"
        else:
            where = "below" if d.saving_pct >= 0 else "above"
            real = f"{abs(d.saving_pct):g}% {where} market Rs {d.market_price:,.0f}"
        ends = f", ends {o.valid_until}" if o.valid_until else ""
        print(f"{d.product.name:<32} Rs {o.price_npr:>9,.0f} at {o.seller or o.source}{was}")
        print(f"{'':<32} {real} | {', '.join(d.verdicts)}{ends}")


def cmd_search(args) -> None:
    store = Store(args.db)
    category = Category(args.category) if args.category else None
    for key in store.search(" ".join(args.text), category, limit=args.limit):
        p = store.product(key)
        price = f"Rs {p.best_price:,.0f}" if p.best_price else "no Nepal price"
        print(f"{p.name:<45} {p.category.value:<11} {price}")


def cmd_reprocess(args) -> None:
    from .pipeline import reprocess
    stats = reprocess(Store(args.db))
    print("rebuilt catalogue from raw records: " + stats.line())


def cmd_quality(args) -> None:
    q = Store(args.db).quality_summary()
    print(f"raw records kept: {q['raw_records']}")
    print("issues: " + (", ".join(f"{v} {k}" for k, v in q["by_kind"].items()) or "none"))
    for r in q["top"]:
        print(f"  {r['n']:>5}  {r['source']:<15} {r['kind']:<13} {r['field']:<16} e.g. {r['example'][:70]}")


def cmd_scrapers(args) -> None:
    """List the fallback chain; with --test URL, fetch that page through each scraper separately."""
    import time

    from .sources.backends import ALL_BACKENDS, block_reason

    failed = []
    for cls in ALL_BACKENDS:
        b = cls()
        missing = b.missing()
        line = f"{b.name:<18} {'browser' if b.browser else 'http':<8} "
        if missing:
            print(line + f"not available: {missing}")
            if b.name in args.require:
                failed.append(b.name)
            continue
        if not args.test:
            print(line + "ready")
            continue
        t0 = time.monotonic()
        try:
            page = b.fetch(args.test, {})
            reason = block_reason(page) or (f"HTTP {page.status}" if page.status >= 400 else None)
            size = len(page.body if isinstance(page.body, bytes) else str(page.body).encode())
            ok = reason is None
            print(line + (f"ok   {size:,} bytes in {time.monotonic() - t0:.1f}s" if ok else f"FAIL {reason}"))
        except Exception as e:
            ok = False
            print(line + f"FAIL {type(e).__name__}: {str(e).splitlines()[0][:120] if str(e) else ''}")
        finally:
            b.close()
        if not ok and b.name in args.require:
            failed.append(b.name)
    if failed:
        sys.exit(f"required scrapers not working: {', '.join(failed)}")


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
    ap.add_argument("--db", help=f"database file (default: {default_db()})")
    ap.add_argument("--sources", help=f"source registry (default: {sources_path()})")
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

    s = sub.add_parser("ask", help='plain words: devicescout ask "photography phone under 1.2 lakh"')
    s.add_argument("text", nargs="+")
    s.add_argument("--top", type=int, default=5)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_ask)

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

    s = sub.add_parser("serve", help="start the web app (opens your browser)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--read-only", action="store_true",
                   help="disable scraping from the UI (use when hosting for the public)")
    s.add_argument("--sample", action="store_true",
                   help="use a built-in sample catalogue of fictional devices (to try the app)")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("schedule", help="scrape all enabled sources every N hours (runs until stopped)")
    s.add_argument("--every", type=_duration, default=_duration("6h"), help="interval, e.g. 30m, 6h, 1d")
    s.add_argument("--jitter", type=_duration, default=_duration("10m"),
                   help="random extra wait so runs don't hit sites at the same minute every day")
    s.add_argument("--limit", type=int, default=300, help="max products per source per run")
    s.add_argument("--delay", type=float, default=2.0, help="seconds between hits to one host")
    s.add_argument("--check-first", action="store_true", help="run a source check before the first scrape")
    s.add_argument("--once", action="store_true", help="one run, then exit (for cron)")
    s.set_defaults(func=cmd_schedule, names=[])

    s = sub.add_parser("deals", help="current deals, checked against other sellers and price history")
    s.add_argument("--category", choices=[c.value for c in Category])
    s.add_argument("--all", action="store_true", help="include store claims we couldn't verify")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(func=cmd_deals)

    s = sub.add_parser("search", help="full-text search the catalogue (names, aliases, chipsets)")
    s.add_argument("text", nargs="+")
    s.add_argument("--category", choices=[c.value for c in Category])
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("reprocess", help="rebuild the catalogue from stored raw records (after parser updates)")
    s.set_defaults(func=cmd_reprocess)

    s = sub.add_parser("quality", help="what cleaning rejected or fixed, and where sources disagree")
    s.set_defaults(func=cmd_quality)

    s = sub.add_parser("scrapers", help="which scrapers in the fallback chain are ready; --test URL tries each")
    s.add_argument("--test", metavar="URL", help="fetch this page through every available scraper")
    s.add_argument("--require", type=lambda v: [x.strip() for x in v.split(",") if x.strip()], default=[],
                   help="comma list of scrapers that must work (exit 1 otherwise), e.g. scrapling-dynamic")
    s.set_defaults(func=cmd_scrapers)

    s = sub.add_parser("export", help="dump the catalogue as CSV or JSON")
    s.add_argument("--format", choices=["csv", "json"], default="csv")
    s.add_argument("--category", choices=[c.value for c in Category])
    s.add_argument("--out")
    s.set_defaults(func=cmd_export)

    # Windows consoles may not encode every character in store names; never crash on output.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = ap.parse_args(argv)
    args.db = args.db or str(default_db())
    args.sources = args.sources or str(sources_path())
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        args.func(args)
    except BrokenPipeError:  # output piped into `head` etc.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)


if __name__ == "__main__":
    main()
