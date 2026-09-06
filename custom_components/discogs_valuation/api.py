"""Client Discogs asynchrone, avec respect strict du rate limit.

Version aiohttp du client valide pendant le spike. Discogs annonce 60 req/min
en authentifie ; on se cale a 55 pour absorber le decalage d'horloge, et on
respecte Retry-After sur 429.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.discogs.com"
RATE_LIMIT = 55
RATE_WINDOW = 60.0
MAX_ATTEMPTS = 4


class DiscogsAuthError(Exception):
    """Token invalide ou revoque."""


class DiscogsApiError(Exception):
    """Erreur API non recuperable."""


class DiscogsClient:
    """Enveloppe minimale autour de l'API Discogs."""

    def __init__(
        self, session: aiohttp.ClientSession, token: str, user_agent: str
    ) -> None:
        self._session = session
        self._headers = {
            "User-Agent": user_agent,
            "Authorization": f"Discogs token={token}",
            "Accept": "application/json",
        }
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()
        self.request_count = 0

    @property
    def session(self) -> aiohttp.ClientSession:
        """Session partagee, reutilisee pour les taux de change."""
        return self._session

    async def _throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            while self._calls and now - self._calls[0] > RATE_WINDOW:
                self._calls.popleft()
            if len(self._calls) >= RATE_LIMIT:
                delay = RATE_WINDOW - (now - self._calls[0]) + 0.25
                _LOGGER.debug("Quota atteint, pause de %.1fs", delay)
                await asyncio.sleep(delay)
                while self._calls and time.monotonic() - self._calls[0] > RATE_WINDOW:
                    self._calls.popleft()
            self._calls.append(time.monotonic())

    async def get(self, path: str, **params: Any) -> Any | None:
        """GET. Renvoie None sur 401/403/404 plutot que de lever.

        Une release absente ou sans suggestion n'est pas une erreur : c'est un
        cas courant qu'on veut compter, pas propager.
        """
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        clean = {k: v for k, v in params.items() if v is not None}

        for attempt in range(MAX_ATTEMPTS):
            await self._throttle()
            self.request_count += 1
            try:
                async with self._session.get(
                    url, params=clean, headers=self._headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 429:
                        delay = float(resp.headers.get("Retry-After", 60))
                        _LOGGER.warning("429 Discogs, pause de %.0fs", delay)
                        await asyncio.sleep(delay)
                        continue
                    if resp.status in (401, 403):
                        # 403 sur une release isolee n'est pas un probleme de
                        # token ; seul /oauth/identity fait foi.
                        if path == "/oauth/identity":
                            raise DiscogsAuthError("Token Discogs refuse")
                        return None
                    if resp.status == 404:
                        return None
                    if resp.status >= 500:
                        if attempt == MAX_ATTEMPTS - 1:
                            return None
                        await asyncio.sleep(2**attempt)
                        continue
                    if resp.status >= 400:
                        return None
                    return await resp.json()
            except TimeoutError:
                if attempt == MAX_ATTEMPTS - 1:
                    raise DiscogsApiError(f"Timeout sur {url}") from None
                await asyncio.sleep(2**attempt)
            except aiohttp.ClientError as err:
                if attempt == MAX_ATTEMPTS - 1:
                    raise DiscogsApiError(f"Erreur reseau sur {url}: {err}") from err
                await asyncio.sleep(2**attempt)
        return None

    # -- endpoints --------------------------------------------------------

    async def identity(self) -> dict:
        """Valide le token et donne le username : l'utilisateur ne le saisit pas."""
        data = await self.get("/oauth/identity")
        if not data:
            raise DiscogsAuthError("Reponse vide de /oauth/identity")
        return data

    async def collection_fields(self, username: str) -> dict | None:
        """Les field_id des conditions sont propres a chaque compte."""
        return await self.get(f"/users/{username}/collection/fields")

    async def collection_page(
        self, username: str, page: int = 1, per_page: int = 100
    ) -> dict | None:
        return await self.get(
            f"/users/{username}/collection/folders/0/releases",
            page=page, per_page=per_page, sort="added", sort_order="desc",
        )

    async def collection_value(self, username: str) -> dict | None:
        return await self.get(f"/users/{username}/collection/value")

    async def price_suggestions(self, release_id: int) -> dict | None:
        """Prix par etat. Ignore curr_abbr : repond dans la devise du compte."""
        return await self.get(f"/marketplace/price_suggestions/{release_id}")

    async def release(self, release_id: int) -> dict | None:
        """Six signaux de confiance en un appel : master_id, have, want,
        num_for_sale, data_quality, country.

        Ne PAS lire lowest_price ici : il est toujours en USD, sans champ
        currency, et ignore curr_abbr. Passer par marketplace_stats.
        """
        return await self.get(f"/releases/{release_id}")

    async def marketplace_stats(
        self, release_id: int, currency: str
    ) -> dict | None:
        """Plancher marche. Respecte curr_abbr et renvoie la devise."""
        return await self.get(
            f"/marketplace/stats/{release_id}", curr_abbr=currency
        )

    async def master_versions(
        self, master_id: int, page: int = 1
    ) -> dict | None:
        return await self.get(
            f"/masters/{master_id}/versions", per_page=100, page=page
        )
