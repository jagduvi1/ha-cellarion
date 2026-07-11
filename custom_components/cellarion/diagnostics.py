"""Diagnostics support for Cellarion."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_EMAIL, CONF_PASSWORD, CONF_TOKEN, CONF_URL

if TYPE_CHECKING:
    from . import CellarionConfigEntry

TO_REDACT = {CONF_TOKEN, CONF_PASSWORD, CONF_EMAIL, CONF_URL}
# Keys inside coordinator.data that identify the instance or hold personal
# content (notification text) — redacted from the diagnostics dump.
DATA_TO_REDACT = {"instance_url", "notifications"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CellarionConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
            "auth_method": "token" if CONF_TOKEN in entry.data else "password",
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "data": (
                async_redact_data(coordinator.data, DATA_TO_REDACT)
                if coordinator.data
                else coordinator.data
            ),
        },
    }
