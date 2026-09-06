"""Conversion de devise via les taux de reference de la BCE.

Necessaire parce que /marketplace/price_suggestions ignore curr_abbr et repond
toujours dans la devise du compte Discogs. Verifie pendant le spike : EUR, USD,
GBP et JPY renvoient tous la meme valeur.

La BCE publie un XML quotidien, gratuit, sans cle d'API, base EUR.
Le taux applique est stocke dans le snapshot pour que l'historique reste
reproductible : sans ca, une variation de change se lirait comme une variation
de valeur de la collection.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import aiohttp

_LOGGER = logging.getLogger(__name__)

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
FX_SOURCE = "ECB eurofxref-daily"


async def fetch_rates(session: aiohttp.ClientSession) -> dict[str, float]:
    """Taux de change base EUR. Renvoie un dict vide en cas d'echec."""
    try:
        async with session.get(
            ECB_URL, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            if resp.status != 200:
                _LOGGER.warning("BCE a repondu %s", resp.status)
                return {}
            payload = await resp.text()
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.warning("Taux BCE indisponibles: %s", err)
        return {}

    rates = {"EUR": 1.0}
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as err:
        _LOGGER.warning("XML BCE illisible: %s", err)
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
    """Facteur multiplicatif pour passer de source vers target.

    Renvoie None si la conversion est impossible : l'appelant doit alors
    conserver la devise source plutot que d'inventer un taux.
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
