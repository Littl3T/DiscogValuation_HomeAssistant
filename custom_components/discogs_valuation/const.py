"""Constantes de l'integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "discogs_valuation"

CONF_TOKEN = "token"
CONF_CURRENCY = "currency"
CONF_INTERVAL = "interval"
CONF_USERNAME = "username"

# Discogs exige un User-Agent identifiant ; un UA generique est bloque.
USER_AGENT = (
    "DiscogsValuationHA/0.1 "
    "+https://github.com/TomDeneyer/DiscogValuation_HomeAssistant"
)

# Le 1h de la demande initiale est ecarte : les suggestions Discogs sont des
# moyennes de ventes passees, elles ne bougent pas a l'heure. Le 1y non plus,
# il ne produit pas de serie exploitable.
INTERVALS: dict[str, timedelta] = {
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
    "1m": timedelta(days=30),
}
DEFAULT_INTERVAL = "1d"

# Devises acceptees par /marketplace/stats. price_suggestions, lui, repond
# toujours dans la devise du compte Discogs : au-dela de celle-ci, on convertit.
CURRENCIES = [
    "EUR", "USD", "GBP", "CAD", "AUD", "JPY",
    "CHF", "MXN", "BRL", "NZD", "SEK", "ZAR",
]
DEFAULT_CURRENCY = "EUR"

# Duree de validite d'un prix en cache. C'est ce qui rend les rafraichissements
# suivants quasi gratuits en quota.
PRICE_TTL = timedelta(days=7)
# Les signaux de confiance bougent encore moins vite que les prix.
META_TTL = timedelta(days=30)
# Une bande de master est quasi statique.
BAND_TTL = timedelta(days=90)

# Budget de requetes pour l'etage 2 par rafraichissement. Evite qu'une grosse
# collection nouvellement ajoutee ne sature le quota d'un coup : les suspects
# non traites le seront au run suivant.
STAGE2_BUDGET = 150

DB_FILENAME = "discogs_valuation.db"
