"""Integration constants."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "discogs_valuation"

CONF_TOKEN = "token"
CONF_CURRENCY = "currency"
CONF_INTERVAL = "interval"
CONF_USERNAME = "username"

# Discogs requires an identifying User-Agent; a generic one gets blocked.
USER_AGENT = (
    "DiscogsValuationHA/0.1 "
    "+https://github.com/Littl3T/DiscogValuation_HomeAssistant"
)

# The hourly interval of the original brief is deliberately left out: Discogs
# suggestions are averages of past sales, they do not move by the hour. Yearly
# is out too, it does not produce a usable series.
INTERVALS: dict[str, timedelta] = {
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
    "1m": timedelta(days=30),
}
DEFAULT_INTERVAL = "1d"

# Currencies accepted by /marketplace/stats. price_suggestions always answers
# in the Discogs account's own currency, so beyond that one we convert.
CURRENCIES = [
    "EUR", "USD", "GBP", "CAD", "AUD", "JPY",
    "CHF", "MXN", "BRL", "NZD", "SEK", "ZAR",
]
DEFAULT_CURRENCY = "EUR"

# How long a cached price stays valid. This is what makes subsequent refreshes
# almost free in quota terms.
PRICE_TTL = timedelta(days=7)
# Confidence signals move even more slowly than prices.
META_TTL = timedelta(days=30)
# A master's price band is close to static.
BAND_TTL = timedelta(days=90)

# Request budget for stage 2 per refresh. Stops a large, newly added collection
# from exhausting the quota in one go: suspects left unprocessed are picked up
# on the next run.
STAGE2_BUDGET = 150

DB_FILENAME = "discogs_valuation.db"
