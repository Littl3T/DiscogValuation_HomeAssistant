"""Reporting services: getting the data out to automations and dashboards.

Each sensor carries a single number. The per-record detail, the full history
and the largest movers live in our SQLite file: these services are the clean
way out, with no need to install the `sql` integration or open the file by
hand.

They return data (SupportsResponse.ONLY): usable in a script via
`response_variable`, in a template, or testable straight from
Developer tools -> Actions.
"""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_REFRESH = "refresh"
SERVICE_GET_HISTORY = "get_history"
SERVICE_GET_ITEMS = "get_items"
SERVICE_GET_FLAGGED = "get_flagged"
SERVICE_GET_MOVERS = "get_movers"
SERVICE_PURGE = "purge"

ORDER_BY = ["value", "value_raw", "confidence", "artist", "title", "year"]

SCHEMA_LIMIT = vol.Schema(
    {vol.Optional("limit", default=50): vol.All(int, vol.Range(min=1, max=5000))}
)
SCHEMA_ITEMS = vol.Schema(
    {
        vol.Optional("limit", default=50): vol.All(int, vol.Range(min=1, max=5000)),
        vol.Optional("order_by", default="value"): vol.In(ORDER_BY),
    }
)
SCHEMA_PURGE = vol.Schema(
    {
        vol.Optional("keep_detail_days", default=90): vol.All(
            int, vol.Range(min=1, max=3650)
        )
    }
)


def _coordinators(hass: HomeAssistant):
    """Every loaded entry. Several Discogs accounts are possible."""
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]


def async_register_services(hass: HomeAssistant) -> None:
    """Register the services once for the domain."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        return

    async def _refresh(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass):
            await coordinator.async_request_refresh()

    async def _get_history(call: ServiceCall) -> ServiceResponse:
        rows: list[dict] = []
        for coordinator in _coordinators(hass):
            rows += await hass.async_add_executor_job(
                coordinator.store.report_history, call.data["limit"]
            )
        return {"history": rows, "count": len(rows)}

    async def _get_items(call: ServiceCall) -> ServiceResponse:
        rows: list[dict] = []
        for coordinator in _coordinators(hass):
            rows += await hass.async_add_executor_job(
                coordinator.store.report_items,
                call.data["limit"],
                None,
                call.data["order_by"],
            )
        return {"items": rows, "count": len(rows)}

    async def _get_flagged(call: ServiceCall) -> ServiceResponse:
        rows: list[dict] = []
        for coordinator in _coordinators(hass):
            rows += await hass.async_add_executor_job(
                coordinator.store.report_flagged
            )
        return {"items": rows, "count": len(rows)}

    async def _get_movers(call: ServiceCall) -> ServiceResponse:
        rows: list[dict] = []
        for coordinator in _coordinators(hass):
            rows += await hass.async_add_executor_job(
                coordinator.store.report_movers, call.data["limit"]
            )
        return {"movers": rows, "count": len(rows)}

    async def _purge(call: ServiceCall) -> ServiceResponse:
        deleted = 0
        for coordinator in _coordinators(hass):
            deleted += await hass.async_add_executor_job(
                coordinator.store.purge, call.data["keep_detail_days"]
            )
        return {"deleted_rows": deleted}

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh)
    hass.services.async_register(
        DOMAIN, SERVICE_GET_HISTORY, _get_history,
        schema=SCHEMA_LIMIT, supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GET_ITEMS, _get_items,
        schema=SCHEMA_ITEMS, supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GET_FLAGGED, _get_flagged,
        schema=vol.Schema({}), supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GET_MOVERS, _get_movers,
        schema=SCHEMA_LIMIT, supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PURGE, _purge,
        schema=SCHEMA_PURGE, supports_response=SupportsResponse.OPTIONAL,
    )
