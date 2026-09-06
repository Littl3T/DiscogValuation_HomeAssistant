"""Currency conversion through the ECB reference rates.

Needed because /marketplace/price_suggestions ignores curr_abbr and always
answers in the Discogs account's own currency. Verified: EUR, USD, GBP and JPY
all return the same value.

The ECB publishes a daily XML feed, free, no API key, EUR-based.
The applied rate is stored in the snapshot so history stays reproducible:
without it, an exchange-rate move would read as a change in the collection's
value.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import aiohttp

_LOGGER = logging.getLogger(__name__)

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
FX_SOURCE = "ECB eurofxref-daily"


async def fetch_rates(session: aiohttp.ClientSession) -> dict[str, float]:
    """EUR-based exchange rates. Returns an empty dict on failure."""
    try:
        async with session.get(
            ECB_URL, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            if resp.status != 200:
                _LOGGER.warning("ECB responded %s", resp.status)
                return {}
            payload = await resp.text()
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.warning("ECB rates unavailable: %s", err)
        return {}

    rates = {"EUR": 1.0}
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as err:
        _LOGGER.warning("Unreadable ECB XML: %s", err)
        return {}

    for node in root.iter():
        currency = node.get("currency")
        rate = node.get("rate")
        if currency and rate:
            try:
                rates[currency] = float(rate)
            except ValueError:
                continue
    return rates


def convert_rate(rates: dict[str, float], source: str, target: str) -> float | None:
    """Multiplier to go from source to target.

    Returns None when conversion is impossible: the caller must then keep the
    source currency rather than invent a rate.
    """
    if source == target:
        return 1.0
    if not rates:
        return None
    src = rates.get(source)
    dst = rates.get(target)
    if not src or not dst:
        return None
    return dst / src
