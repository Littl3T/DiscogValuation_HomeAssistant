"""Discogs Collection Valuation integration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DiscogsClient
from .const import (
    CONF_CURRENCY,
    CONF_INTERVAL,
    CONF_TOKEN,
    CONF_USERNAME,
    DB_FILENAME,
    DEFAULT_CURRENCY,
    DEFAULT_INTERVAL,
    DOMAIN,
    INTERVALS,
    USER_AGENT,
)
from .coordinator import ValuationCoordinator
from .services import async_register_services
from .store import ValuationStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

# Alias for the typed config entry.
#
# Written without the `type X = ...` syntax (PEP 695), which requires Python
# 3.12: on an older Home Assistant instance that is a SyntaxError at import
# time, and the integration fails to load with no visible message. Likewise,
# ConfigEntry is only generic from HA 2024.6 onwards, hence the TYPE_CHECKING
# guard.
if TYPE_CHECKING:
    ValuationConfigEntry = ConfigEntry[ValuationCoordinator]
else:
    ValuationConfigEntry = ConfigEntry


async def async_setup_entry(
    hass: HomeAssistant, entry: ValuationConfigEntry
) -> bool:
    """Set the integration up for one config entry."""
    session = async_get_clientsession(hass)
    client = DiscogsClient(session, entry.data[CONF_TOKEN], USER_AGENT)

    db_path = Path(hass.config.path(DB_FILENAME))
    store = await hass.async_add_executor_job(ValuationStore, db_path)

    interval_key = entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)
    coordinator = ValuationCoordinator(
        hass=hass,
        client=client,
        store=store,
        username=entry.data[CONF_USERNAME],
        currency=entry.options.get(CONF_CURRENCY, DEFAULT_CURRENCY),
        update_interval=INTERVALS.get(interval_key, INTERVALS[DEFAULT_INTERVAL]),
    )

    # The first snapshot of a large collection can take several minutes on a
    # cold cache, and the entry stays on "Initializing" for that whole time.
    # We still wait for it: the sensors have nothing to show before it lands,
    # and creating them empty would only produce a screen full of "Unknown".
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    async_register_services(hass)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ValuationConfigEntry
) -> bool:
    """Tear the integration down."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: ValuationConfigEntry) -> None:
    """Reload when the user changes the interval or the currency."""
    await hass.config_entries.async_reload(entry.entry_id)
