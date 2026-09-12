"""Serve the bundled Lovelace card and register it as a dashboard resource.

HACS-only: a core integration cannot ship frontend code, so the core export
(tools/export_core.py) drops this module together with the www/ folder.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

FRONTEND_URL_BASE = "/cellarion-files"
CARD_FILENAME = "cellarion-card.js"
CARD_REGISTERED: HassKey[bool] = HassKey(f"{DOMAIN}_card_registered")


async def async_register_card(hass: HomeAssistant) -> None:
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
