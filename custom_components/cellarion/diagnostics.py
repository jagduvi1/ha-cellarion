"""Diagnostics support for Cellarion."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACCOUNT_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_URL,
)

if TYPE_CHECKING:
    from .coordinator import CellarionConfigEntry

TO_REDACT = {CONF_TOKEN, CONF_PASSWORD, CONF_EMAIL, CONF_URL, CONF_ACCOUNT_ID}
# Keys inside coordinator.data that identify the instance, the account or
# individual records, or hold personal content (notification text) — redacted
# from the diagnostics dump. Raw server objects (cellars) carry the owner id
# and member list, so those keys are covered at any depth.
DATA_TO_REDACT = {
    "instance_url",
    "notifications",
    "user",
    "owner",
    "members",
    "userColors",
    "_id",
    "id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CellarionConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
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
