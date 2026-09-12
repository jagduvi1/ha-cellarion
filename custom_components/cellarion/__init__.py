"""The Cellarion integration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util.hass_dict import HassKey
import voluptuous as vol

from .api import (
    CellarionApiClient,
    CellarionApiError,
    CellarionAuthError,
    CellarionScopeError,
)
from .const import (
    CONF_EMAIL,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import CellarionCoordinator
from .push import async_push_listener, push_issue_id

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

FRONTEND_URL_BASE = "/cellarion-files"
CARD_FILENAME = "cellarion-card.js"
CARD_REGISTERED: HassKey[bool] = HassKey(f"{DOMAIN}_card_registered")

SERVICE_CONSUME_BOTTLE = "consume_bottle"
CONSUME_REASONS = ["drank", "gifted", "sold", "other"]
# Cellarion bottle ids are MongoDB ObjectIds: exactly 24 hex characters. The
# id is interpolated into a request path, so anything else is refused here.
BOTTLE_ID_PATTERN = r"^[0-9a-fA-F]{24}$"

SERVICE_CONSUME_SCHEMA = vol.Schema(
    {
        vol.Required("bottle_id"): vol.All(cv.string, vol.Match(BOTTLE_ID_PATTERN)),
        vol.Optional("reason", default="drank"): vol.In(CONSUME_REASONS),
        vol.Optional("rating"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional("note"): vol.All(cv.string, vol.Length(max=1000)),
        vol.Optional("entry_id"): cv.string,
    }
)

type CellarionConfigEntry = ConfigEntry[CellarionCoordinator]


async def _async_consume_bottle(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the cellarion.consume_bottle service."""
    entries: list[CellarionConfigEntry] = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_accounts")

    if entry_id := call.data.get("entry_id"):
        matches = [entry for entry in entries if entry.entry_id == entry_id]
        if not matches:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_entry",
                translation_placeholders={"entry_id": entry_id},
            )
        entry = matches[0]
    elif len(entries) == 1:
        entry = entries[0]
    else:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="multiple_accounts")

    coordinator = entry.runtime_data
    try:
        await coordinator.client.consume_bottle(
            call.data["bottle_id"],
            reason=call.data["reason"],
            rating=call.data.get("rating"),
            note=call.data.get("note"),
        )
    except CellarionScopeError as err:
        # Token is valid but was created without the consume scope — the
        # user has to mint a new one; say so instead of a generic failure
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="consume_scope_missing",
            translation_placeholders={"error": str(err)},
        ) from err
    except CellarionAuthError as err:
        # Revoked token / changed password: start the reauth prompt now
        # rather than waiting for the next poll to notice
        entry.async_start_reauth(hass)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="consume_auth_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    except CellarionApiError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="consume_failed",
            translation_placeholders={"error": str(err)},
        ) from err

    await coordinator.async_request_refresh()


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services; they exist even before an entry is configured."""

    async def _handle_consume(call: ServiceCall) -> None:
        await _async_consume_bottle(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_CONSUME_BOTTLE,
        _handle_consume,
        schema=SERVICE_CONSUME_SCHEMA,
    )
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the bundled Lovelace card and add it as a dashboard resource."""
    if hass.data.get(CARD_REGISTERED):
        return

    www_dir = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL_BASE, str(www_dir), cache_headers=True)]
    )
    # Set only once the static path is up: if registration raised above,
    # the next entry (or reload) gets another try instead of a dead card.
    hass.data[CARD_REGISTERED] = True

    integration = await async_get_integration(hass, DOMAIN)
    card_url = f"{FRONTEND_URL_BASE}/{CARD_FILENAME}"
    versioned_url = f"{card_url}?v={integration.version}"

    try:
        lovelace = hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None) if lovelace else None
        if resources is None:
            _LOGGER.info(
                "Lovelace resources unavailable; add %s as a dashboard "
                "resource manually to use the Cellarion card",
                card_url,
            )
            return
        if not resources.loaded:
            await resources.async_load()
            resources.loaded = True

        for item in resources.async_items():
            if item.get("url", "").split("?")[0] == card_url:
                if item["url"] != versioned_url and hasattr(resources, "async_update_item"):
                    await resources.async_update_item(item["id"], {"url": versioned_url})
                return

        if hasattr(resources, "async_create_item"):
            await resources.async_create_item({"res_type": "module", "url": versioned_url})
            _LOGGER.debug("Registered Cellarion card resource %s", versioned_url)
        else:
            # YAML-mode dashboards can't be modified programmatically
            _LOGGER.info(
                "Dashboards are in YAML mode; add %s as a module resource "
                "manually to use the Cellarion card",
                card_url,
            )
    except Exception:
        _LOGGER.warning(
            "Could not register the Cellarion card automatically; add %s "
            "as a dashboard resource manually",
            card_url,
            exc_info=True,
        )


async def async_setup_entry(hass: HomeAssistant, entry: CellarionConfigEntry) -> bool:
    """Set up Cellarion from a config entry."""
    await _async_register_card(hass)

    if not entry.data.get(CONF_TOKEN):
        # Entries made before 1.10 against a server without API tokens still
        # carry the account password. Password storage is gone: the reauth
        # flow mints a scoped token (and drops the password) instead.
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="password_entry_retired"
        )

    client = CellarionApiClient(
        session=async_get_clientsession(hass),
        url=entry.data[CONF_URL],
        email=entry.data.get(CONF_EMAIL),
        token=entry.data[CONF_TOKEN],
    )

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = CellarionCoordinator(hass, entry, client, scan_interval, entry.data[CONF_URL])
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_create_background_task(
        hass, async_push_listener(coordinator), name="cellarion_push_listener"
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: CellarionConfigEntry) -> None:
    """Handle options update — reload the integration."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CellarionConfigEntry) -> bool:
    """Unload a config entry."""
    # The push listener (a config-entry background task) is cancelled by HA;
    # its repair issue, if any, is ours to clear.
    ir.async_delete_issue(hass, DOMAIN, push_issue_id(entry.entry_id))
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: CellarionConfigEntry) -> None:
    """Clean up when the entry is deleted for good.

    The scoped API token minted for this entry is revoked on the server
    (DELETE /api/tokens/self, Cellarion 1.220+). Older servers answer 403 and
    the token has to be revoked by hand, as the README explains; removal
    itself never fails because of it.
    """
    ir.async_delete_issue(hass, DOMAIN, push_issue_id(entry.entry_id))
    if not (token := entry.data.get(CONF_TOKEN)):
        return
    client = CellarionApiClient(
        session=async_get_clientsession(hass), url=entry.data[CONF_URL], token=token
    )
    if await client.revoke_own_token():
        _LOGGER.info("Revoked the Cellarion API token that belonged to this entry")
    else:
        _LOGGER.warning(
            "The Cellarion API token for this entry could not be revoked automatically; "
            "revoke it in Cellarion under Settings → API tokens if it is still listed"
        )
