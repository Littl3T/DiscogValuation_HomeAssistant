"""Config flow: three fields, nothing more.

The username is never asked for: it is derived from the token via
/oauth/identity. Condition field ids are resolved automatically on the first
snapshot.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import DiscogsApiError, DiscogsAuthError, DiscogsClient
from .const import (
    CONF_CURRENCY,
    CONF_INTERVAL,
    CONF_TOKEN,
    CONF_USERNAME,
    CURRENCIES,
    DEFAULT_CURRENCY,
    DEFAULT_INTERVAL,
    DOMAIN,
    INTERVALS,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_TOKEN, default=defaults.get(CONF_TOKEN, "")
            ): str,
            vol.Required(
                CONF_INTERVAL, default=defaults.get(CONF_INTERVAL, DEFAULT_INTERVAL)
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(INTERVALS),
                    translation_key="interval",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_CURRENCY, default=defaults.get(CONF_CURRENCY, DEFAULT_CURRENCY)
            ): SelectSelector(
                SelectSelectorConfig(
                    options=CURRENCIES, mode=SelectSelectorMode.DROPDOWN
                )
            ),
        }
    )


class DiscogsValuationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Adding the integration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = DiscogsClient(session, user_input[CONF_TOKEN], USER_AGENT)
            try:
                identity = await client.identity()
            except DiscogsAuthError:
                errors["base"] = "invalid_auth"
            except DiscogsApiError:
                errors["base"] = "cannot_connect"
            else:
                username = identity["username"]
                await self.async_set_unique_id(str(identity["id"]))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Discogs collection of {username}",
                    data={
                        CONF_TOKEN: user_input[CONF_TOKEN],
                        CONF_USERNAME: username,
                    },
                    options={
                        CONF_INTERVAL: user_input[CONF_INTERVAL],
                        CONF_CURRENCY: user_input[CONF_CURRENCY],
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return DiscogsValuationOptionsFlow()


class DiscogsValuationOptionsFlow(OptionsFlow):
    """Changing the interval and the currency after the fact."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_INTERVAL,
                        default=options.get(CONF_INTERVAL, DEFAULT_INTERVAL),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=list(INTERVALS),
                            translation_key="interval",
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_CURRENCY,
                        default=options.get(CONF_CURRENCY, DEFAULT_CURRENCY),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=CURRENCIES, mode=SelectSelectorMode.DROPDOWN
                        )
                    ),
                }
            ),
        )
