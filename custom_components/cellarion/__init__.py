"""The Cellarion integration.

Comment markers delimit the code that belongs to an optional feature
(services, push, the bundled card). tools/export_core.py uses them to
produce the trimmed layout a Home Assistant core submission starts from;
the HACS build always ships everything.
"""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from pycellarion import CellarionClient

from .const import (
    CONF_EMAIL,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import CellarionConfigEntry, CellarionCoordinator

# @feature frontend
from .frontend import async_register_card

# @endfeature
# @feature push
from .push import async_push_listener, push_issue_id

# @endfeature
# @feature services
from .services import async_setup_services

# @endfeature

__all__ = ["CellarionConfigEntry"]

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the component: services exist even before an entry is configured."""
    # @feature services
    async_setup_services(hass)
    # @endfeature
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CellarionConfigEntry) -> bool:
    """Set up Cellarion from a config entry."""
    # @feature frontend
    await async_register_card(hass)
    # @endfeature

    if not entry.data.get(CONF_TOKEN):
        # Entries made before 1.10 against a server without API tokens still
        # carry the account password. Password storage is gone: the reauth
        # flow mints a scoped token (and drops the password) instead.
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="password_entry_retired"
        )

    client = CellarionClient(
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

    # @feature push
    entry.async_create_background_task(
        hass, async_push_listener(coordinator), name="cellarion_push_listener"
    )
    # @endfeature

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: CellarionConfigEntry) -> None:
    """Handle options update — reload the integration."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CellarionConfigEntry) -> bool:
    """Unload a config entry."""
    # @feature push
    # The push listener (a config-entry background task) is cancelled by HA;
    # its repair issue, if any, is ours to clear.
    ir.async_delete_issue(hass, DOMAIN, push_issue_id(entry.entry_id))
    # @endfeature
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: CellarionConfigEntry) -> None:
    """Clean up when the entry is deleted for good.

    The scoped API token minted for this entry is revoked on the server
    (DELETE /api/tokens/self, Cellarion 1.220+). Older servers answer 403 and
    the token has to be revoked by hand, as the README explains; removal
    itself never fails because of it.
    """
    # @feature push
    ir.async_delete_issue(hass, DOMAIN, push_issue_id(entry.entry_id))
    # @endfeature
    if not (token := entry.data.get(CONF_TOKEN)):
        return
    client = CellarionClient(
        session=async_get_clientsession(hass), url=entry.data[CONF_URL], token=token
    )
    if await client.revoke_own_token():
        _LOGGER.info("Revoked the Cellarion API token that belonged to this entry")
    else:
        _LOGGER.warning(
            "The Cellarion API token for this entry could not be revoked automatically; "
            "revoke it in Cellarion under Settings → API tokens if it is still listed"
        )
