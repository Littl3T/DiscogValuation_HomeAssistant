"""Asynchronous Discogs client, with strict rate-limit compliance.

aiohttp version of the client validated during the exploratory phase. Discogs
advertises 60 req/min when authenticated; we settle at 55 to absorb clock skew,
and honour Retry-After on a 429.
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
    """Token invalid or revoked."""


class DiscogsApiError(Exception):
    """Unrecoverable API error."""


class DiscogsClient:
    """Minimal wrapper around the Discogs API."""

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
        """Shared session, reused for exchange rates."""
        return self._session

    async def _throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            while self._calls and now - self._calls[0] > RATE_WINDOW:
                self._calls.popleft()
            if len(self._calls) >= RATE_LIMIT:
                delay = RATE_WINDOW - (now - self._calls[0]) + 0.25
                _LOGGER.debug("Quota reached, pausing for %.1fs", delay)
                await asyncio.sleep(delay)
                while self._calls and time.monotonic() - self._calls[0] > RATE_WINDOW:
                    self._calls.popleft()
            self._calls.append(time.monotonic())

    async def get(self, path: str, **params: Any) -> Any | None:
        """GET. Returns None on 401/403/404 rather than raising.

        A missing release, or one with no price suggestion, is not an error:
        it is a common case we want to count, not propagate.
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
                        _LOGGER.warning("Discogs 429, pausing for %.0fs", delay)
                        await asyncio.sleep(delay)
                        continue
                    if resp.status in (401, 403):
                        # A 403 on a single release is not a token problem;
                        # only /oauth/identity is authoritative on that.
                        if path == "/oauth/identity":
                            raise DiscogsAuthError("Discogs rejected the token")
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
                    raise DiscogsApiError(f"Timeout on {url}") from None
                await asyncio.sleep(2**attempt)
            except aiohttp.ClientError as err:
                if attempt == MAX_ATTEMPTS - 1:
                    raise DiscogsApiError(f"Network error on {url}: {err}") from err
                await asyncio.sleep(2**attempt)
        return None

    # -- endpoints --------------------------------------------------------

    async def identity(self) -> dict:
        """Validate the token and get the username: the user never types it."""
        data = await self.get("/oauth/identity")
        if not data:
            raise DiscogsAuthError("Empty response from /oauth/identity")
        return data

    async def collection_fields(self, username: str) -> dict | None:
        """Condition field ids are specific to each account."""
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
        """Price per condition. Ignores curr_abbr: answers in the account's
        own currency."""
        return await self.get(f"/marketplace/price_suggestions/{release_id}")

    async def release(self, release_id: int) -> dict | None:
        """Six confidence signals in one call: master_id, have, want,
        num_for_sale, data_quality, country.

        Do NOT read lowest_price here: it is always in USD, carries no currency
        field, and ignores curr_abbr. Go through marketplace_stats instead.
        """
        return await self.get(f"/releases/{release_id}")

    async def marketplace_stats(
        self, release_id: int, currency: str
    ) -> dict | None:
        """Market floor. Honours curr_abbr and returns the currency."""
        return await self.get(
            f"/marketplace/stats/{release_id}", curr_abbr=currency
        )

    async def master_versions(
        self, master_id: int, page: int = 1
    ) -> dict | None:
        return await self.get(
            f"/masters/{master_id}/versions", per_page=100, page=page
        )
