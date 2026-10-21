"""Serve the bundled Lovelace cards and register them as dashboard resources.

HACS-only: a core integration cannot ship frontend code, so the core export
(tools/export_core.py) drops this module together with the www/ folder.

Also home of the label-image proxy the inventory card uses. Cellarion serves
its photos same-origin only (Cross-Origin-Resource-Policy), so the browser
cannot show them on Home Assistant's origin; Home Assistant fetches the
thumbnail server-side and hands it to signed-in users only.
"""

from __future__ import annotations

from collections import OrderedDict
import logging
from pathlib import Path

from aiohttp import web
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import HomeAssistantView
from homeassistant.loader import async_get_integration
from homeassistant.util.hass_dict import HassKey
from pycellarion import IMAGE_PATH_RE, CellarionApiError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

FRONTEND_URL_BASE = "/cellarion-files"
CARD_FILENAMES = ("cellarion-card.js", "cellarion-inventory-card.js")
CARD_REGISTERED: HassKey[bool] = HassKey(f"{DOMAIN}_card_registered")

# Thumbnails are ~400x640 WebP, a few tens of kB each; keep the most recent
# ones so a second device or a reopened card doesn't ask Cellarion again.
IMAGE_CACHE_ITEMS = 128
IMAGE_CACHE_BYTES = 16 * 1024 * 1024
# What the proxy passes on. Anything else (SVG with script, HTML) is refused:
# the response is served on Home Assistant's own origin.
IMAGE_TYPES = {"image/webp", "image/jpeg", "image/png"}


class CellarionImageView(HomeAssistantView):
    """GET /api/cellarion/image/<entry_id>/<processed|originals>/<file>."""

    url = "/api/cellarion/image/{entry_id}/{kind}/{filename}"
    name = "api:cellarion:image"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Hold the hass object and an empty cache."""
        self.hass = hass
        self._cache: OrderedDict[tuple[str, str], tuple[bytes, str]] = OrderedDict()
        self._cache_bytes = 0

    def _remember(self, key: tuple[str, str], body: bytes, content_type: str) -> None:
        self._cache[key] = (body, content_type)
        self._cache_bytes += len(body)
        while self._cache and (
            len(self._cache) > IMAGE_CACHE_ITEMS or self._cache_bytes > IMAGE_CACHE_BYTES
        ):
            _, (old, _) = self._cache.popitem(last=False)
            self._cache_bytes -= len(old)

    async def get(
        self, request: web.Request, entry_id: str, kind: str, filename: str
    ) -> web.Response:
        """Return one label thumbnail of a loaded Cellarion entry."""
        entry = self.hass.config_entries.async_get_entry(entry_id)
        path = f"/api/uploads/thumbs/{kind}/{filename}"
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
            or not IMAGE_PATH_RE.fullmatch(path)
        ):
            return web.Response(status=404)

        key = (entry_id, path)
        if cached := self._cache.get(key):
            self._cache.move_to_end(key)
            body, content_type = cached
        else:
            try:
                body, content_type = await entry.runtime_data.client.get_image(path)
            except (CellarionApiError, ValueError) as err:
                _LOGGER.debug("Label image %s unavailable: %s", path, err)
                return web.Response(status=502)
            content_type = content_type.split(";")[0].strip().lower()
            if content_type not in IMAGE_TYPES:
                _LOGGER.debug("Label image %s has type %s; refused", path, content_type)
                return web.Response(status=502)
            self._remember(key, body, content_type)

        return web.Response(
            body=body,
            content_type=content_type,
            headers={
                "Cache-Control": "private, max-age=86400",
                "X-Content-Type-Options": "nosniff",
            },
        )


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the bundled Lovelace cards and add them as dashboard resources."""
    if hass.data.get(CARD_REGISTERED):
        return

    www_dir = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL_BASE, str(www_dir), cache_headers=True)]
    )
    hass.http.register_view(CellarionImageView(hass))
    # Set only once the static path is up: if registration raised above,
    # the next entry (or reload) gets another try instead of a dead card.
    hass.data[CARD_REGISTERED] = True

    integration = await async_get_integration(hass, DOMAIN)
    for filename in CARD_FILENAMES:
        await _async_register_resource(hass, f"{FRONTEND_URL_BASE}/{filename}", integration.version)


async def _async_register_resource(hass: HomeAssistant, card_url: str, version: str | None) -> None:
    """Add (or re-version) one card as a module resource; never fail setup."""
    versioned_url = f"{card_url}?v={version}"
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
