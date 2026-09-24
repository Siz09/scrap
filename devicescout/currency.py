"""Convert offer prices to NPR so everything is compared in the buyer's currency.

The NPR is pegged to the Indian rupee at 1 INR = 1.6 NPR. Other rates float: update
them (or set DEVICESCOUT_RATES='{"USD": 141.5}') before trusting converted prices.
Converted international prices ignore customs duty, VAT and shipping, so they are
only a reference for "is the Nepali price reasonable?", never a buyable price.
"""

from __future__ import annotations

import json
import logging
import os

RATES_TO_NPR: dict[str, float] = {
    "NPR": 1.0,
    "INR": 1.6,     # fixed peg
    "USD": 140.0,   # approximate; update
    "EUR": 160.0,   # approximate; update
    "GBP": 185.0,   # approximate; update
    "AED": 38.0,    # approximate; update
}
try:
    # Empty is normal: docker compose passes unset variables through as "".
    RATES_TO_NPR.update({k.upper(): float(v) for k, v in json.loads(os.getenv("DEVICESCOUT_RATES") or "{}").items()})
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
