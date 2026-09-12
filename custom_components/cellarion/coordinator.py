"""Data update coordinator for Cellarion."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from pycellarion import (
    CellarionApiError,
    CellarionAuthError,
    CellarionClient,
    CellarionScopeError,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# The service-status sensor is an enum; anything the health endpoint says
# outside this set is reported as "unknown" so the entity never rejects a state.
HEALTH_STATES = ["ok", "degraded", "unreachable", "unknown"]


def _as_dict(value: Any) -> dict[str, Any]:
    """Return a dict for a payload section that may be missing or null."""
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """Return a list for a payload section that may be missing or null."""
    return value if isinstance(value, list) else []


def _health_state(value: Any) -> str:
    """Map the server's health status onto the sensor's enum options."""
    status = str(value).lower() if value else "unknown"
    return status if status in HEALTH_STATES else "unknown"


def _parse_peak_bottles(peak_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Shape the peak-bottle payload into the sensor's attribute list.

    Kept small and pure so the caller can treat any error (missing keys,
    unexpected types) as "no peak data" without failing the whole update.
    """
    peak_items = _as_list(_as_dict(peak_data.get("bottles")).get("items"))
    return sorted(
        (
            {
                "id": b.get("_id"),
                "name": _as_dict(b.get("wineDefinition")).get("name", "Unknown"),
                "producer": _as_dict(b.get("wineDefinition")).get("producer", ""),
                "vintage": b.get("vintage") or "NV",
                "drink_to": b.get("drinkTo"),
            }
            for b in peak_items
            if isinstance(b, dict)
        ),
        # Drink first what leaves its window first; None (unknown) sorts last
        key=lambda x: (x["drink_to"] is None, x["drink_to"]),
    )


class CellarionCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls the Cellarion API."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: CellarionClient,
        scan_interval: int,
        url: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Cellarion",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.url = url

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Cellarion API."""
        try:
            stats_data = await self.client.get_stats_overview()
            cellars_data = await self.client.get_cellars()
            notifications_data = await self.client.get_notifications()
            health_data = await self.client.get_health()
            # Best-effort: powers the "ready to drink" list. A failure here —
            # an older self-hosted server (API error) or an unexpected payload
            # shape (parse error) — must not break the other sensors, so both
            # the fetch and the parse are guarded.
            try:
                peak_data = await self.client.get_peak_bottles()
                peak_bottles = _parse_peak_bottles(peak_data)
            except CellarionApiError as err:
                _LOGGER.debug("Peak bottles unavailable: %s", err)
                peak_bottles = []
            except (AttributeError, KeyError, TypeError, ValueError) as err:
                _LOGGER.debug("Peak bottles payload unexpected: %s", err)
                peak_bottles = []
        except CellarionScopeError as err:
            # Token valid but missing the read scope — user must provide a
            # properly scoped token; the reauth flow lets them do that.
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="scope_missing",
                translation_placeholders={"error": str(err)},
            ) from err
        except CellarionAuthError as err:
            # Stop polling and trigger HA's reauth flow. Retrying a bad
            # password every poll would trip Cellarion's account lockout.
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except CellarionApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        # Every section is normalised so a null or missing block from the
        # server degrades to "no data" instead of failing the state write.
        stats = _as_dict(stats_data.get("stats"))

        return {
            "overview": _as_dict(stats.get("overview")),
            "maturity": _as_dict(stats.get("maturity")),
            "pace": _as_dict(stats.get("pace")),
            "cellar_breakdown": _as_list(stats.get("cellarBreakdown")),
            "by_type": _as_dict(stats.get("byType")),
            "by_country": _as_list(stats.get("byCountry")),
            "top_producers": _as_list(stats.get("topProducers")),
            "urgency_ladder": _as_list(stats.get("urgencyLadder")),
            "cellars": _as_list(cellars_data.get("cellars")),
            "cellar_count": cellars_data.get("count") or 0,
            "notifications": _as_list(notifications_data.get("notifications")),
            "unread_count": notifications_data.get("unreadCount") or 0,
            "health": _health_state(health_data.get("status")),
            "instance_url": self.url,
            "peak_bottles": peak_bottles,
        }
