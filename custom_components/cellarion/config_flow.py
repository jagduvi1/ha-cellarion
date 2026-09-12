"""Config flow for Cellarion integration."""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from urllib.parse import urlparse

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import UNDEFINED
import voluptuous as vol

from .api import (
    CellarionApiClient,
    CellarionApiError,
    CellarionAuthError,
    CellarionScopeError,
    CellarionTokensNotSupported,
)
from .const import (
    CONF_ACCOUNT_ID,
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


def _normalize_url(raw: str) -> str | None:
    """Clean a user-entered instance URL, or return None if it isn't valid.

    Requires an http(s) scheme and a host — rejects typos like a bare host or
    an accidental "javascript:"/"file:" paste before any credential is sent.
    Credentials embedded in the URL (https://user:pass@host) are refused too:
    aiohttp cannot combine them with the bearer header, and they would end up
    in logs.
    """
    url = raw.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return url


def _host_title(url: str) -> str:
    """Entry title for a token-based entry: the instance host."""
    return f"Cellarion ({urlparse(url).netloc or url})"


def _unique_id(url: str, account_id: str | None, fallback: str) -> str:
    """Identity of an entry: the account on that instance.

    Prefers the server's account id so the same account can't be added twice
    with different tokens or via token and password. Older servers that
    can't say who the credential belongs to fall back to a credential-based
    id (email, or a hash of the token).
    """
    return f"{url}_{account_id}" if account_id else f"{url}_{fallback}"


async def _validate_token(
    hass: HomeAssistant, url: str, token: str
) -> tuple[str | None, str | None]:
    """Try a read call with the token.

    Returns (error_key, account_id): error_key is None on success, and
    account_id is the identity to verify against on reauth (None when the
    server/token can't provide one).
    """
    client = CellarionApiClient(async_get_clientsession(hass), url, token=token)
    try:
        await client.get_stats_overview()
    except CellarionScopeError:
        return "token_scope", None
    except CellarionAuthError:
        return "invalid_token", None
    except CellarionApiError as err:
        _LOGGER.error("Cannot connect to Cellarion at %s: %s", url, err)
        return "cannot_connect", None
    except Exception:
        _LOGGER.exception("Unexpected error validating Cellarion token")
        return "unknown", None
    return None, await client.get_account_id()


def _account_mismatch(entry: ConfigEntry | None, new_account_id: str | None) -> bool:
    """True only when the new credential provably belongs to a different account.

    Requires both a previously stored account id and a freshly fetched one —
    if either is unknown (older server, token without identity scope, or an
    entry created before this check existed) the reauth proceeds unguarded,
    exactly as before.
    """
    if not entry or not new_account_id:
        return False
    stored = entry.data.get(CONF_ACCOUNT_ID)
    return bool(stored) and stored != new_account_id


class CellarionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Cellarion."""

    VERSION = 1

    @property
    def _is_reauth(self) -> bool:
        return self.source == SOURCE_REAUTH

    @property
    def _entry(self) -> ConfigEntry | None:
        """The entry being re-authenticated or reconfigured, if any."""
        if self.source == SOURCE_REAUTH:
            return self._get_reauth_entry()
        if self.source == SOURCE_RECONFIGURE:
            return self._get_reconfigure_entry()
        return None

    # ── Entry points ─────────────────────────────────────────────────

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose the authentication method."""
        return self.async_show_menu(step_id="user", menu_options=["token", "password"])

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauth when credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how to re-authenticate.

        The menu's step_id must map to this handler — HA resumes menu
        flows by re-invoking async_step_<step_id>.
        """
        return self.async_show_menu(step_id="reauth_confirm", menu_options=["token", "password"])

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure the entry (change URL or credentials)."""
        return self.async_show_menu(step_id="reconfigure", menu_options=["token", "password"])

    # ── API token path ───────────────────────────────────────────────

    async def async_step_token(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Authenticate with a pasted personal API token."""
        errors: dict[str, str] = {}
        entry = self._entry

        if user_input is not None:
            # Reauth keeps the entry URL; new setup and reconfigure take it
            # from the form
            url = (
                entry.data[CONF_URL]
                if entry and self._is_reauth
                else _normalize_url(user_input[CONF_URL])
            )
            token = user_input[CONF_TOKEN].strip()

            if url is None:
                errors["base"] = "invalid_url"
            else:
                error, account_id = await _validate_token(self.hass, url, token)
                token_hash = hashlib.sha256(token.encode()).hexdigest()[:12]
                unique_id = _unique_id(url, account_id, f"token_{token_hash}")
                if error:
                    errors["base"] = error
                elif _account_mismatch(entry, account_id):
                    errors["base"] = "account_mismatch"
                elif entry:
                    return await self._async_finish_existing(
                        entry,
                        {
                            CONF_URL: url,
                            CONF_EMAIL: entry.data.get(CONF_EMAIL),
                            CONF_TOKEN: token,
                            CONF_ACCOUNT_ID: account_id or entry.data.get(CONF_ACCOUNT_ID),
                        },
                        unique_id=unique_id if account_id else None,
                        title=_host_title(url) if not entry.data.get(CONF_EMAIL) else None,
                    )
                else:
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()
                    data = {CONF_URL: url, CONF_TOKEN: token}
                    if account_id:
                        data[CONF_ACCOUNT_ID] = account_id
                    return self.async_create_entry(
                        title=_host_title(url),
                        data=data,
                        options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
                    )

        if entry and self._is_reauth:
            schema = vol.Schema({vol.Required(CONF_TOKEN): str})
        else:
            default_url = entry.data[CONF_URL] if entry else DEFAULT_URL
            schema = vol.Schema(
                {
                    vol.Required(CONF_URL, default=default_url): str,
                    vol.Required(CONF_TOKEN): str,
                }
            )
        return self.async_show_form(step_id="token", data_schema=schema, errors=errors)

    # ── Email & password path (mints a token when supported) ────────

    async def async_step_password(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Authenticate with email+password; mint and store an API token."""
        errors: dict[str, str] = {}
        entry = self._entry

        if user_input is not None:
            url = (
                entry.data[CONF_URL]
                if entry and self._is_reauth
                else _normalize_url(user_input[CONF_URL])
            )

            if url is None:
                errors["base"] = "invalid_url"
            else:
                email = user_input[CONF_EMAIL].strip()
                password = user_input[CONF_PASSWORD]

                data: dict[str, Any] | None = None
                account_id: str | None = None
                client = CellarionApiClient(
                    async_get_clientsession(self.hass), url, email, password
                )
                try:
                    await client.authenticate()
                    # The JWT from authenticate() can read the identity endpoint
                    # even when a scoped token later cannot.
                    account_id = await client.get_account_id()
                    try:
                        name = f"Home Assistant ({self.hass.config.location_name})"
                        token = await client.async_create_api_token(name[:60], TOKEN_SCOPES)
                        data = {CONF_URL: url, CONF_EMAIL: email, CONF_TOKEN: token}
                        _LOGGER.debug("Minted a scoped API token; password not stored")
                    except CellarionTokensNotSupported:
                        # Older self-hosted server — fall back to password auth
                        data = {
                            CONF_URL: url,
                            CONF_EMAIL: email,
                            CONF_PASSWORD: password,
                        }
                    # Keep any previously stored id if the server can't supply one
                    resolved = account_id or (entry.data.get(CONF_ACCOUNT_ID) if entry else None)
                    if resolved:
                        data[CONF_ACCOUNT_ID] = resolved
                except CellarionAuthError:
                    errors["base"] = "invalid_auth"
                except CellarionApiError as err:
                    _LOGGER.error("Cannot connect to Cellarion at %s: %s", url, err)
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error during setup")
                    errors["base"] = "unknown"

                if data is not None:
                    unique_id = _unique_id(url, account_id, email)
                    if _account_mismatch(entry, account_id):
                        errors["base"] = "account_mismatch"
                    elif entry:
                        return await self._async_finish_existing(
                            entry,
                            data,
                            unique_id=unique_id if account_id else None,
                            title=f"Cellarion ({email})",
                        )
                    else:
                        await self.async_set_unique_id(unique_id)
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title=f"Cellarion ({email})",
                            data=data,
                            options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
                        )

        if entry and self._is_reauth:
            schema = vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=entry.data.get(CONF_EMAIL, "")): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            )
        else:
            default_url = entry.data[CONF_URL] if entry else DEFAULT_URL
            schema = vol.Schema(
                {
                    vol.Required(CONF_URL, default=default_url): str,
                    vol.Required(
                        CONF_EMAIL,
                        default=entry.data.get(CONF_EMAIL, "") if entry else "",
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            )
        return self.async_show_form(step_id="password", data_schema=schema, errors=errors)

    # ── Helpers ──────────────────────────────────────────────────────

    async def _async_finish_existing(
        self,
        entry: ConfigEntry,
        data: dict[str, Any],
        *,
        unique_id: str | None,
        title: str | None,
    ) -> ConfigFlowResult:
        """Store new credentials on the existing entry and reload.

        On reconfigure the identity may change (new URL, or a server that
        now reports an account id). The new identity must not collide with
        another entry; the entry's own id is of course allowed.
        """
        if unique_id and unique_id != entry.unique_id:
            other = self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, unique_id)
            if other and other.entry_id != entry.entry_id:
                return self.async_abort(reason="already_configured")
        return self.async_update_reload_and_abort(
            entry,
            data={k: v for k, v in data.items() if v is not None},
            unique_id=unique_id or UNDEFINED,
            title=title or UNDEFINED,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CellarionOptionsFlow:
        """Return the options flow handler."""
        return CellarionOptionsFlow()


class CellarionOptionsFlow(OptionsFlow):
    """Handle options for Cellarion."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)),
                }
            ),
        )
