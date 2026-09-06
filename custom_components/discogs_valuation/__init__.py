"""Integration Discogs Collection Valuation."""

from __future__ import annotations

import logging
from pathlib import Path

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

type ValuationConfigEntry = ConfigEntry[ValuationCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: ValuationConfigEntry
) -> bool:
    """Monte l'integration pour une entree de configuration."""
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

    # Le premier snapshot d'une grosse collection peut prendre plusieurs
    # minutes (cache froid). On ne bloque pas le demarrage de HA dessus.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    async_register_services(hass)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ValuationConfigEntry
) -> bool:
    """Demonte l'integration."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: ValuationConfigEntry) -> None:
    """Recharge quand l'utilisateur change l'intervalle ou la devise."""
    await hass.config_entries.async_reload(entry.entry_id)
