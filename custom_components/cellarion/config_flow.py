"""Config flow for Cellarion integration."""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

try:  # HA 2024.4+
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:  # pragma: no cover — older HA
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult

from .api import (
    CellarionApiClient,
    CellarionApiError,
    CellarionAuthError,
    CellarionScopeError,
    CellarionTokensNotSupported,
)
from .const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    TOKEN_SCOPES,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_URL = "https://cellarion.app"


async def _validate_token(hass: HomeAssistant, url: str, token: str) -> str | None:
    """Try a read call with the token. Return an error key or None."""
    client = CellarionApiClient(
        async_get_clientsession(hass), url, token=token
    )
    try:
        await client.get_stats_overview()
    except CellarionScopeError:
        return "token_scope"
    except CellarionAuthError:
        return "invalid_token"
    except CellarionApiError as err:
        _LOGGER.error("Cannot connect to Cellarion at %s: %s", url, err)
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error validating Cellarion token")
        return "unknown"
    return None


class CellarionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Cellarion."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    # ── Entry points ─────────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the authentication method."""
        return self.async_show_menu(
            step_id="user", menu_options=["token", "password"]
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
        """Choose how to re-authenticate.

        The menu's step_id must map to this handler — HA resumes menu
        flows by re-invoking async_step_<step_id>.
        """
        return self.async_show_menu(
            step_id="reauth_confirm", menu_options=["token", "password"]
        )

    # ── API token path ───────────────────────────────────────────────

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Authenticate with a pasted personal API token."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry

        if user_input is not None:
            url = (
                entry.data[CONF_URL]
                if entry
                else user_input[CONF_URL].rstrip("/")
            )
            token = user_input[CONF_TOKEN].strip()

            error = await _validate_token(self.hass, url, token)
            if error:
                errors["base"] = error
            elif entry:
                return await self._async_finish_reauth(
                    {
                        CONF_URL: url,
                        CONF_EMAIL: entry.data.get(CONF_EMAIL),
                        CONF_TOKEN: token,
                    }
                )
            else:
                token_id = hashlib.sha256(token.encode()).hexdigest()[:12]
                await self.async_set_unique_id(f"{url}_token_{token_id}")
                self._abort_if_unique_id_configured()
                host = urlparse(url).netloc or url
                return self.async_create_entry(
                    title=f"Cellarion ({host})",
                    data={CONF_URL: url, CONF_TOKEN: token},
                    options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
                )

        schema = (
            vol.Schema({vol.Required(CONF_TOKEN): str})
            if entry
            else vol.Schema(
                {
                    vol.Required(CONF_URL, default=DEFAULT_URL): str,
                    vol.Required(CONF_TOKEN): str,
                }
            )
        )
        return self.async_show_form(
            step_id="token", data_schema=schema, errors=errors
        )

    # ── Email & password path (mints a token when supported) ────────

    async def async_step_password(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Authenticate with email+password; mint and store an API token."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry

        if user_input is not None:
            url = (
                entry.data[CONF_URL]
                if entry
                else user_input[CONF_URL].rstrip("/")
            )
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            if not entry:
                await self.async_set_unique_id(f"{url}_{email}")
                self._abort_if_unique_id_configured()

            data: dict[str, Any] | None = None
            client = CellarionApiClient(
                async_get_clientsession(self.hass), url, email, password
            )
            try:
                await client.authenticate()
                try:
                    name = f"Home Assistant ({self.hass.config.location_name})"
                    token = await client.async_create_api_token(
                        name[:60], TOKEN_SCOPES
                    )
                    data = {CONF_URL: url, CONF_EMAIL: email, CONF_TOKEN: token}
                    _LOGGER.debug("Minted a scoped API token; password not stored")
                except CellarionTokensNotSupported:
                    # Older self-hosted server — fall back to password auth
                    data = {
                        CONF_URL: url,
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                    }
            except CellarionAuthError:
                errors["base"] = "invalid_auth"
            except CellarionApiError as err:
                _LOGGER.error("Cannot connect to Cellarion at %s: %s", url, err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = "unknown"

            if data is not None:
                if entry:
                    return await self._async_finish_reauth(data)
                return self.async_create_entry(
                    title=f"Cellarion ({email})",
                    data=data,
                    options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
                )

        if entry:
            schema = vol.Schema(
                {
                    vol.Required(
                        CONF_EMAIL, default=entry.data.get(CONF_EMAIL, "")
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            )
        else:
            schema = vol.Schema(
                {
                    vol.Required(CONF_URL, default=DEFAULT_URL): str,
                    vol.Required(CONF_EMAIL): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            )
        return self.async_show_form(
            step_id="password", data_schema=schema, errors=errors
        )

    # ── Helpers ──────────────────────────────────────────────────────

    async def _async_finish_reauth(
        self, data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Store new credentials on the existing entry and reload."""
        entry = self._reauth_entry
        assert entry is not None
        self.hass.config_entries.async_update_entry(
            entry, data={k: v for k, v in data.items() if v is not None}
        )
        await self.hass.config_entries.async_reload(entry.entry_id)
        return self.async_abort(reason="reauth_successful")

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
