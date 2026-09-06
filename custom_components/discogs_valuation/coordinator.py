"""Orchestration of a full snapshot. No user intervention.

The request budget is the guiding constraint: Discogs caps at 60/min, so
anything cacheable is cached, and stage 2 is capped per run.

Cost of a refresh, 145-pressing collection:
  cold cache  ~590 requests (~12 min)  first run only
  warm cache  ~5 requests              every run after that
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DiscogsAuthError, DiscogsClient
from .const import (
    BAND_TTL,
    META_TTL,
    PRICE_TTL,
    STAGE2_BUDGET,
)
from .fx import FX_SOURCE, convert_rate, fetch_rates
from .store import ValuationStore
from .valuation import (
    ReleaseSignals,
    confidence_score,
    collection_totals,
    is_suspect,
    nm_base_from_suggestions,
    valuate_item,
)

_LOGGER = logging.getLogger(__name__)

MEDIA_FIELD = "media condition"
SLEEVE_FIELD = "sleeve condition"
BAND_SAMPLE = 8


class ValuationCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Chains collection, valuation, correction and writing."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: DiscogsClient,
        store: ValuationStore,
        username: str,
        currency: str,
        update_interval,
    ) -> None:
        super().__init__(
            hass, _LOGGER, name="Discogs valuation", update_interval=update_interval
        )
        self._client = client
        self._store = store
        self._username = username
        self._currency = currency
        self._media_field_id: int | None = None
        self._sleeve_field_id: int | None = None

    @property
    def store(self) -> ValuationStore:
        """Expose persistence to the reporting services."""
        return self._store

    # -- steps ------------------------------------------------------------

    async def _resolve_fields(self) -> None:
        """Condition field ids are specific to each Discogs account."""
        if self._media_field_id is not None:
            return
        data = await self._client.collection_fields(self._username)
        for field in (data or {}).get("fields", []):
            name = (field.get("name") or "").strip().lower()
            if name == MEDIA_FIELD:
                self._media_field_id = field["id"]
            elif name == SLEEVE_FIELD:
                self._sleeve_field_id = field["id"]

    async def _fetch_collection(self) -> list[dict]:
        items: list[dict] = []
        page = 1
        while True:
            data = await self._client.collection_page(self._username, page=page)
            if not data:
                break
            items.extend(data.get("releases", []))
            pages = data.get("pagination", {}).get("pages", 1)
            if page >= pages:
                break
            page += 1
        return items

    def _condition(self, item: dict, field_id: int | None) -> str | None:
        if field_id is None:
            return None
        for note in item.get("notes") or []:
            if note.get("field_id") == field_id:
                return (note.get("value") or "").strip() or None
        return None

    async def _price(
        self, release_id: int, cache: dict[int, float]
    ) -> tuple[float | None, str | None]:
        """NM base price. The API returns 8 conditions but they all derive from
        a single base: we store one value only."""
        if release_id in cache:
            return cache[release_id], None
        data = await self._client.price_suggestions(release_id)
        base = nm_base_from_suggestions(data or {})
        currency = None
        if data:
            first = next(iter(data.values()), None)
            if isinstance(first, dict):
                currency = first.get("currency")
        await self.hass.async_add_executor_job(
            self._store.store_price, release_id, base, currency
        )
        return base, currency

    async def _signals(
        self, release_id: int, cache: dict[int, Any]
    ) -> ReleaseSignals:
        """Confidence signals. Two calls: /releases for the metadata, and
        /marketplace/stats for the floor in the right currency.

        lowest_price from /releases is unusable: always in USD, with no
        currency field, and curr_abbr is ignored there.
        """
        cached = cache.get(release_id)
        if cached is not None:
            return ReleaseSignals(
                release_id=release_id,
                master_id=cached["master_id"],
                have=cached["have"] or 0,
                want=cached["want"] or 0,
                num_for_sale=cached["num_for_sale"] or 0,
                data_quality=cached["data_quality"],
                floor_price=cached["floor_price"],
            )

        release = await self._client.release(release_id) or {}
        stats = await self._client.marketplace_stats(release_id, self._currency) or {}
        community = release.get("community") or {}
        lowest = stats.get("lowest_price") or {}

        meta = {
            "master_id": release.get("master_id") or None,
            "have": community.get("have") or 0,
            "want": community.get("want") or 0,
            "num_for_sale": stats.get("num_for_sale")
            or release.get("num_for_sale")
            or 0,
            "data_quality": release.get("data_quality"),
            "country": release.get("country"),
            "floor_price": lowest.get("value"),
            "floor_currency": lowest.get("currency"),
        }
        await self.hass.async_add_executor_job(
            self._store.store_meta, release_id, meta
        )
        return ReleaseSignals(
            release_id=release_id,
            master_id=meta["master_id"],
            have=meta["have"],
            want=meta["want"],
            num_for_sale=meta["num_for_sale"],
            data_quality=meta["data_quality"],
            floor_price=meta["floor_price"],
        )

    async def _band(self, master_id: int, cache: dict[int, float]) -> float | None:
        """Stage 2: median of the most-owned sibling pressings.

        Costs ~10 requests, hence the selective trigger and the long cache.
        """
        if master_id in cache:
            return cache[master_id]

        versions: list[dict] = []
        page = 1
        while page <= 2:  # the first 2 pages are enough for the most-owned
            data = await self._client.master_versions(master_id, page=page)
            if not data:
                break
            versions.extend(data.get("versions", []))
            if page >= data.get("pagination", {}).get("pages", 1):
                break
            page += 1
        if not versions:
            return None

        versions.sort(
            key=lambda v: (v.get("stats") or {})
            .get("community", {})
            .get("in_collection", 0),
            reverse=True,
        )
        values: list[float] = []
        for version in versions[:BAND_SAMPLE]:
            data = await self._client.price_suggestions(version["id"])
            base = nm_base_from_suggestions(data or {})
            if base:
                values.append(base)

        median = statistics.median(values) if values else None
        await self.hass.async_add_executor_job(
            self._store.store_band, master_id, len(versions), len(values), median
        )
        cache[master_id] = median
        return median

    # -- main loop --------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._snapshot()
        except DiscogsAuthError as err:
            raise UpdateFailed(f"Discogs rejected the token: {err}") from err
        except Exception as err:  # noqa: BLE001 - surfaced cleanly in HA
            raise UpdateFailed(f"Snapshot failed: {err}") from err

    async def _snapshot(self) -> dict[str, Any]:
        await self._resolve_fields()
        items = await self._fetch_collection()
        if not items:
            raise UpdateFailed("Collection empty or unreadable")

        price_cache, meta_cache, band_cache = await asyncio.gather(
            self.hass.async_add_executor_job(
                self._store.cached_prices, PRICE_TTL.total_seconds()
            ),
            self.hass.async_add_executor_job(
                self._store.cached_meta, META_TTL.total_seconds()
            ),
            self.hass.async_add_executor_job(
                self._store.cached_bands, BAND_TTL.total_seconds()
            ),
        )

        release_ids = sorted({i["basic_information"]["id"] for i in items})

        # Stage 0: base prices
        bases: dict[int, float | None] = {}
        source_currency: str | None = None
        for release_id in release_ids:
            base, currency = await self._price(release_id, price_cache)
            bases[release_id] = base
            source_currency = source_currency or currency

        # Stage 1: confidence signals
        signals: dict[int, ReleaseSignals] = {}
        for release_id in release_ids:
            signals[release_id] = await self._signals(release_id, meta_cache)

        # Stage 2: suspects only, within budget
        provisional = sum(
            (bases.get(i["basic_information"]["id"]) or 0) for i in items
        )
        bands: dict[int, float | None] = {}
        spent = 0
        candidates = sorted(
            release_ids,
            key=lambda r: -(bases.get(r) or 0),
        )
        for release_id in candidates:
            base = bases.get(release_id)
            signal = signals[release_id]
            if base is None or not signal.master_id:
                continue
            share = base / provisional if provisional else 0.0
            score, _ = confidence_score(signal)
            if not is_suspect(signal, score, share):
                continue
            if signal.master_id in band_cache:
                bands[release_id] = band_cache[signal.master_id]
                continue
            if spent >= STAGE2_BUDGET:
                _LOGGER.debug("Stage 2 budget spent, deferred to the next run")
                break
            bands[release_id] = await self._band(signal.master_id, band_cache)
            spent += BAND_SAMPLE + 2

        # Valuation
        results = []
        rows = []
        for item in items:
            info = item["basic_information"]
            release_id = info["id"]
            result = valuate_item(
                instance_id=item["instance_id"],
                signals=signals[release_id],
                nm_base=bases.get(release_id),
                media_condition=self._condition(item, self._media_field_id),
                sleeve_condition=self._condition(item, self._sleeve_field_id),
                band_median=bands.get(release_id),
            )
            results.append(result)
            labels = info.get("labels") or [{}]
            formats = info.get("formats") or [{}]
            rows.append(
                {
                    "instance_id": result.instance_id,
                    "release_id": release_id,
                    "artist": ", ".join(
                        a["name"] for a in info.get("artists", [])
                    ),
                    "title": info.get("title"),
                    "year": info.get("year"),
                    "label": labels[0].get("name"),
                    "catno": labels[0].get("catno"),
                    "format": formats[0].get("name"),
                    "media_condition": result.media_condition,
                    "sleeve_condition": result.sleeve_condition,
                    "nm_base": result.nm_base,
                    "value": result.value,
                    "value_raw": result.value_raw,
                    "floor_price": signals[release_id].floor_price,
                    "confidence": result.confidence,
                    "flags": json.dumps(result.flags, ensure_ascii=False),
                    "price_source": result.price_source,
                }
            )

        totals = collection_totals(results)

        # Currency conversion when the Discogs account is not in the requested
        # currency. The rate is frozen into the snapshot.
        source_currency = source_currency or self._currency
        fx_rate = 1.0
        fx_source = None
        if source_currency != self._currency:
            rates = await fetch_rates(self._client.session)
            rate = convert_rate(rates, source_currency, self._currency)
            if rate is None:
                _LOGGER.warning(
                    "Cannot convert %s -> %s, values kept in %s",
                    source_currency, self._currency, source_currency,
                )
            else:
                fx_rate, fx_source = rate, FX_SOURCE
                for row in rows:
                    for key in ("value", "value_raw", "nm_base", "floor_price"):
                        if row[key] is not None:
                            row[key] = round(row[key] * fx_rate, 2)
                for key in ("total_value", "total_raw", "avg_value"):
                    totals[key] = round(totals[key] * fx_rate, 2)

        effective_currency = (
            self._currency if fx_source or source_currency == self._currency
            else source_currency
        )

        await self.hass.async_add_executor_job(
            self._store.write_snapshot,
            effective_currency, source_currency, fx_rate, fx_source, rows, totals,
        )
        like_for_like = await self.hass.async_add_executor_job(
            self._store.like_for_like_delta
        )
        history = await self.hass.async_add_executor_job(self._store.latest_history)

        floor_total = round(
            sum(
                (signals[i["basic_information"]["id"]].floor_price or 0) * fx_rate
                for i in items
            ),
            2,
        )

        _LOGGER.info(
            "Snapshot: %s items, %s %s, %s requests",
            totals["item_count"], totals["total_value"],
            effective_currency, self._client.request_count,
        )

        return {
            **totals,
            "currency": effective_currency,
            "source_currency": source_currency,
            "fx_rate": fx_rate,
            "floor_total": floor_total,
            "delta_abs": history["delta_abs"] if history else None,
            "delta_pct": history["delta_pct"] if history else None,
            "delta_items": history["delta_items"] if history else None,
            "like_for_like": like_for_like,
            "requests": self._client.request_count,
        }
