"""Convert offer prices to NPR so everything is compared in the buyer's currency.

Rates are refreshed on every scrape run (fetch_rates): Nepal Rastra Bank's published
rates first, a public exchange-rate service as the backup, and the built-in values
below only if both are unreachable. The NPR is pegged to the Indian rupee at 1.6.
DEVICESCOUT_RATES='{"USD": 141.5}' pins rates by hand.

Converted international prices ignore customs duty, VAT and shipping: they tell you what
a device costs abroad, not what a Nepali shop will charge.
"""

from __future__ import annotations

import json
import logging
import os

RATES_INFO: dict = {"source": "built-in estimates", "date": None}

RATES_TO_NPR: dict[str, float] = {
    "NPR": 1.0,
    "INR": 1.6,     # fixed peg
    "USD": 140.0,   # approximate; update
    "EUR": 160.0,   # approximate; update
    "GBP": 185.0,   # approximate; update
    "AED": 38.0,    # approximate; update
}
_PINNED: dict[str, float] = {}
try:
    # Empty is normal: docker compose passes unset variables through as "".
    _PINNED = {k.upper(): float(v) for k, v in json.loads(os.getenv("DEVICESCOUT_RATES") or "{}").items()}
    RATES_TO_NPR.update(_PINNED)
except (ValueError, AttributeError) as e:
    logging.getLogger(__name__).warning("ignoring DEVICESCOUT_RATES (expected JSON like {\"USD\": 141.2}): %s", e)

_ALIASES = {"RS": "NPR", "RS.": "NPR", "NRS": "NPR", "NRS.": "NPR", "रु": "NPR", "₹": "INR", "$": "USD", "€": "EUR", "£": "GBP"}


def to_npr(amount: float | None, currency: str | None) -> float | None:
    if amount is None:
        return None
    code = (currency or "NPR").strip().upper()
    code = _ALIASES.get(code, code)
    rate = RATES_TO_NPR.get(code)
    return round(amount * rate, 2) if rate else None


def symbol_for(currency: str | None) -> str:
    code = (currency or "NPR").strip().upper()
    code = _ALIASES.get(code, code)
    return {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "NPR": "Rs "}.get(code, f"{code} ")


def set_rates(rates: dict[str, float], source: str, date: str | None) -> None:
    """Use these rates (NPR per 1 unit) from now on in this process."""
    clean = {k.upper(): float(v) for k, v in rates.items() if v and float(v) > 0}
    clean["NPR"] = 1.0
    clean["INR"] = 1.6             # the peg, whatever a feed rounds it to
    RATES_TO_NPR.update(clean)
    RATES_TO_NPR.update(_PINNED)   # hand-pinned rates always win
    RATES_INFO.update({"source": source, "date": date})


def rates_info() -> dict:
    return {**RATES_INFO, "rates": {k: RATES_TO_NPR[k] for k in sorted(RATES_TO_NPR)}}


def _get_json(url: str, timeout: float = 15):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "DeviceScout (price comparison, personal use)",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _from_nrb() -> tuple[dict[str, float], str] | None:
    """Nepal Rastra Bank's official daily rates (buy/sell per `unit`; INR, JPY ... per 100)."""
    from datetime import date, timedelta
    today = date.today()
    frm = (today - timedelta(days=7)).isoformat()
    data = _get_json(f"https://www.nrb.org.np/api/forex/v1/rates?page=1&per_page=10&from={frm}&to={today.isoformat()}")
    days = (data.get("data") or {}).get("payload") or []
    if not days:
        return None
    latest = max(days, key=lambda d: d.get("date", ""))
    rates = {}
    for r in latest.get("rates") or []:
        cur = r.get("currency") or {}
        try:
            unit = float(cur.get("unit") or 1)
            mid = (float(r["buy"]) + float(r["sell"])) / 2
        except (KeyError, TypeError, ValueError):
            continue
        if cur.get("iso3") and mid > 0:
            rates[cur["iso3"].upper()] = round(mid / unit, 4)
    return (rates, latest.get("date")) if rates else None


def _from_open_er() -> tuple[dict[str, float], str] | None:
    """Backup: open.er-api.com (free, no key); rates are per 1 NPR, so invert."""
    data = _get_json("https://open.er-api.com/v6/latest/NPR")
    per_npr = data.get("rates") or {}
    rates = {k.upper(): round(1 / v, 4) for k, v in per_npr.items() if isinstance(v, (int, float)) and v > 0}
    when = (data.get("time_last_update_utc") or "")[:16]
    return (rates, when) if rates else None


def fetch_rates() -> dict | None:
    """Today's rates, or None if no feed could be reached (the previous rates stay in use)."""
    log = logging.getLogger(__name__)
    for name, fn in (("Nepal Rastra Bank", _from_nrb), ("open.er-api.com", _from_open_er)):
        try:
            got = fn()
        except Exception as e:                       # offline, blocked, changed format
            log.warning("exchange rates from %s unavailable: %s", name, e)
            continue
        if got:
            rates, when = got
            set_rates(rates, name, when)
            return rates_info()
    return None


def apply_stored(info: dict | None) -> None:
    """Rates another process saved (the scraper stores them; the website reads them)."""
    if info and info.get("rates"):
        set_rates(info["rates"], info.get("source") or "stored", info.get("date"))
