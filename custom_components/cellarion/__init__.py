"""The Cellarion integration."""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

from .api import CellarionApiClient, CellarionApiError
from .const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_URL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import CellarionCoordinator
from .push import async_push_listener

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

FRONTEND_URL_BASE = "/cellarion-files"
CARD_FILENAME = "cellarion-card.js"

SERVICE_CONSUME_BOTTLE = "consume_bottle"
CONSUME_REASONS = ["drank", "gifted", "sold", "other"]

SERVICE_CONSUME_SCHEMA = vol.Schema(
    {
        vol.Required("bottle_id"): cv.string,
        vol.Optional("reason", default="drank"): vol.In(CONSUME_REASONS),
        vol.Optional("rating"): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional("note"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)


async def _async_consume_bottle(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the cellarion.consume_bottle service."""
    coordinators: dict[str, CellarionCoordinator] = hass.data.get(DOMAIN, {})
    if not coordinators:
        raise ServiceValidationError("No Cellarion accounts are configured")

    if entry_id := call.data.get("entry_id"):
        coordinator = coordinators.get(entry_id)
        if coordinator is None:
            raise ServiceValidationError(f"Unknown config entry: {entry_id}")
    elif len(coordinators) == 1:
        coordinator = next(iter(coordinators.values()))
    else:
        raise ServiceValidationError(
            "Multiple Cellarion accounts are configured; pass entry_id"
        )

    try:
        await coordinator.client.consume_bottle(
            call.data["bottle_id"],
            reason=call.data["reason"],
            rating=call.data.get("rating"),
            note=call.data.get("note"),
        )
    except CellarionApiError as err:
        raise HomeAssistantError(f"Could not consume bottle: {err}") from err

    await coordinator.async_request_refresh()


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the bundled Lovelace card and add it as a dashboard resource."""
    if hass.data.get(f"{DOMAIN}_card_registered"):
        return
    hass.data[f"{DOMAIN}_card_registered"] = True

    www_dir = Path(__file__).parent / "www"
    try:
        # HA 2024.6+
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(FRONTEND_URL_BASE, str(www_dir), cache_headers=True)]
        )
    except ImportError:
        hass.http.register_static_path(FRONTEND_URL_BASE, str(www_dir), True)

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
                if item["url"] != versioned_url and hasattr(
                    resources, "async_update_item"
                ):
                    await resources.async_update_item(
                        item["id"], {"url": versioned_url}
                    )
                return

        if hasattr(resources, "async_create_item"):
            await resources.async_create_item(
                {"res_type": "module", "url": versioned_url}
            )
            _LOGGER.debug("Registered Cellarion card resource %s", versioned_url)
        else:
            # YAML-mode dashboards can't be modified programmatically
            _LOGGER.info(
                "Dashboards are in YAML mode; add %s as a module resource "
                "manually to use the Cellarion card",
                card_url,
            )
    except Exception:  # noqa: BLE001 — the card is optional, never block setup
        _LOGGER.warning(
            "Could not register the Cellarion card automatically; add %s "
            "as a dashboard resource manually",
            card_url,
            exc_info=True,
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Cellarion from a config entry."""
    await _async_register_card(hass)

    session = async_get_clientsession(hass)
    client = CellarionApiClient(
        session=session,
        url=entry.data[CONF_URL],
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
    )

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = CellarionCoordinator(
        hass, client, scan_interval, entry.data[CONF_URL]
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    if not hass.services.has_service(DOMAIN, SERVICE_CONSUME_BOTTLE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CONSUME_BOTTLE,
            partial(_async_consume_bottle, hass),
            schema=SERVICE_CONSUME_SCHEMA,
        )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_create_background_task(
        hass, async_push_listener(coordinator), name="cellarion_push_listener"
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update — reload the integration."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    ):
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_CONSUME_BOTTLE)
    return unload_ok
