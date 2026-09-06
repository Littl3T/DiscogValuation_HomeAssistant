"""Client Discogs minimal, avec respect strict du rate limit.

Ecrit pour le spike, mais concu pour devenir le coeur de l'integration HA :
aucune dependance a Home Assistant, pas d'etat global, tout passe par le
client. La seule adaptation future sera de passer requests -> aiohttp.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.discogs.com"

# Discogs exige un User-Agent identifiant. Un UA generique (python-requests,
# curl, un navigateur) se fait bloquer.
USER_AGENT = "DiscogsValuationHA/0.1 +https://github.com/TomDeneyer/DiscogValuation_HomeAssistant"

# Discogs annonce 60 req/min en authentifie. On se garde une marge : la fenetre
# est glissante cote serveur et notre horloge n'est pas la sienne.
RATE_LIMIT = 55
RATE_WINDOW = 60.0


class DiscogsError(RuntimeError):
    """Erreur API non recuperable."""

    def __init__(self, status: int, message: str, url: str) -> None:
        super().__init__(f"HTTP {status} sur {url} : {message}")
        self.status = status
        self.url = url


@dataclass
class Result:
    """Reponse enveloppee : on veut distinguer 'refuse' de 'vide'."""

    ok: bool
    status: int
    data: Any = None
    error: str = ""


@dataclass
class DiscogsClient:
    token: str
    timeout: float = 20.0
    _session: requests.Session = field(default_factory=requests.Session, repr=False)
    _calls: deque[float] = field(default_factory=deque, repr=False)

    #: Derniers en-tetes de quota vus, pour le rapport final.
    last_limit: int | None = None
    last_remaining: int | None = None
    total_requests: int = 0

    def __post_init__(self) -> None:
        self._session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Authorization": f"Discogs token={self.token}",
                "Accept": "application/json",
            }
        )

    # -- rate limiting ----------------------------------------------------

    def _throttle(self) -> None:
        """Bloque tant que la fenetre glissante est pleine."""
        now = time.monotonic()
        while self._calls and now - self._calls[0] > RATE_WINDOW:
            self._calls.popleft()
        if len(self._calls) >= RATE_LIMIT:
            wait = RATE_WINDOW - (now - self._calls[0]) + 0.25
            if wait > 0:
                _LOGGER.debug("Quota atteint, pause de %.1fs", wait)
                time.sleep(wait)
                self._throttle()
                return
        self._calls.append(time.monotonic())

    # -- transport --------------------------------------------------------

    def get(self, path: str, **params: Any) -> Result:
        """GET brut. Ne leve que sur erreur reseau ou 5xx repetee.

        Les 401/403/404 sont renvoyes comme Result(ok=False) : pour le spike,
        un refus est une information, pas un crash.
        """
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        params = {k: v for k, v in params.items() if v is not None}

        for attempt in range(4):
            self._throttle()
            self.total_requests += 1
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as err:
                if attempt == 3:
                    raise DiscogsError(0, str(err), url) from err
                time.sleep(2**attempt)
                continue

            self._record_quota(resp)

            if resp.status_code == 429:
                # Quota depasse malgre notre throttle : on respecte Retry-After.
                delay = float(resp.headers.get("Retry-After", 60))
                _LOGGER.warning("429 recu, pause de %.0fs", delay)
                time.sleep(delay)
                continue

            if resp.status_code >= 500:
                if attempt == 3:
                    return Result(False, resp.status_code, error=resp.text[:200])
                time.sleep(2**attempt)
                continue

            if resp.status_code >= 400:
                return Result(False, resp.status_code, error=_extract_message(resp))

            return Result(True, resp.status_code, data=resp.json())

        return Result(False, 429, error="quota epuise apres 4 tentatives")

    def _record_quota(self, resp: requests.Response) -> None:
        limit = resp.headers.get("X-Discogs-Ratelimit")
        remaining = resp.headers.get("X-Discogs-Ratelimit-Remaining")
        if limit is not None:
            self.last_limit = int(limit)
        if remaining is not None:
            self.last_remaining = int(remaining)

    # -- endpoints --------------------------------------------------------

    def identity(self) -> Result:
        """Valide le token et renvoie le username : l'utilisateur n'a pas a le saisir."""
        return self.get("/oauth/identity")

    def collection_fields(self, username: str) -> Result:
        """Les field_id des champs Media/Sleeve Condition sont propres a chaque compte."""
        return self.get(f"/users/{username}/collection/fields")

    def collection_page(
        self, username: str, page: int = 1, per_page: int = 100, folder: int = 0
    ) -> Result:
        """Folder 0 = 'All'. Renvoie les exemplaires (instances), pas les releases."""
        return self.get(
            f"/users/{username}/collection/folders/{folder}/releases",
            page=page,
            per_page=per_page,
            sort="added",
            sort_order="desc",
        )

    def collection_value(self, username: str) -> Result:
        """Agregat officiel Discogs : min/median/max, 1 seule requete."""
        return self.get(f"/users/{username}/collection/value")

    def price_suggestions(self, release_id: int, curr_abbr: str | None = None) -> Result:
        """LE endpoint critique : prix suggere par etat, pour ce pressage precis."""
        return self.get(
            f"/marketplace/price_suggestions/{release_id}", curr_abbr=curr_abbr
        )

    def marketplace_stats(self, release_id: int, curr_abbr: str = "EUR") -> Result:
        """Repli : plancher marche reel, mais sans distinction d'etat."""
        return self.get(f"/marketplace/stats/{release_id}", curr_abbr=curr_abbr)

    def release(self, release_id: int, curr_abbr: str = "EUR") -> Result:
        return self.get(f"/releases/{release_id}", curr_abbr=curr_abbr)


def _extract_message(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("message", resp.text[:200]))
    except ValueError:
        return resp.text[:200]
