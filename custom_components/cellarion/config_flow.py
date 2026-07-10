"""Config flow for Cellarion integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

try:  # HA 2024.4+
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:  # pragma: no cover — older HA
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult

from .api import CellarionApiClient, CellarionApiError, CellarionAuthError
from .const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL, default="https://cellarion.app"): str,
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _validate_credentials(
    hass, url: str, email: str, password: str
) -> str | None:
    """Try to authenticate. Return an error key or None on success."""
    session = async_get_clientsession(hass)
    client = CellarionApiClient(session, url, email, password)
    try:
        await client.authenticate()
    except CellarionAuthError:
        return "invalid_auth"
    except CellarionApiError as err:
        _LOGGER.error("Cannot connect to Cellarion at %s: %s", url, err)
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error validating Cellarion credentials")
        return "unknown"
    return None


class CellarionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Cellarion."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            # Check for duplicate entries
            await self.async_set_unique_id(f"{url}_{email}")
            self._abort_if_unique_id_configured()

            error = await _validate_credentials(self.hass, url, email, password)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=f"Cellarion ({email})",
                    data={
                        CONF_URL: url,
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                    },
                    options={
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth when credentials stop working."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password and revalidate."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        assert entry is not None

        if user_input is not None:
            error = await _validate_credentials(
                self.hass,
                entry.data[CONF_URL],
                entry.data[CONF_EMAIL],
                user_input[CONF_PASSWORD],
            )
            if error:
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={CONF_EMAIL: entry.data[CONF_EMAIL]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CellarionOptionsFlow:
        """Return the options flow handler."""
        return CellarionOptionsFlow(config_entry)


class CellarionOptionsFlow(OptionsFlow):
    """Handle options for Cellarion."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        # Assigning self.config_entry is deprecated (removed in HA 2025.12);
        # keep our own reference so this works on every HA version.
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self._entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)
                    ),
                }
            ),
        )
