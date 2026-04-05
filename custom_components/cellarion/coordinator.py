"""Data update coordinator for Cellarion."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import CellarionApiClient, CellarionApiError, CellarionAuthError

_LOGGER = logging.getLogger(__name__)


class CellarionCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls the Cellarion API."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: CellarionApiClient,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Cellarion",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Cellarion API."""
        try:
            stats_data = await self.client.get_stats_overview()
            cellars_data = await self.client.get_cellars()
            notifications_data = await self.client.get_notifications()
            health_data = await self.client.get_health()
        except CellarionAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except CellarionApiError as err:
            raise UpdateFailed(f"API error: {err}") from err

        stats = stats_data.get("stats", {})
        overview = stats.get("overview", {})
        maturity = stats.get("maturity", {})
        pace = stats.get("pace", {})

        return {
            "overview": overview,
            "maturity": maturity,
            "pace": pace,
            "cellar_breakdown": stats.get("cellarBreakdown", []),
            "by_type": stats.get("byType", {}),
            "by_country": stats.get("byCountry", []),
            "top_producers": stats.get("topProducers", []),
            "urgency_ladder": stats.get("urgencyLadder", []),
            "cellars": cellars_data.get("cellars", []),
            "cellar_count": cellars_data.get("count", 0),
            "notifications": notifications_data.get("notifications", []),
            "unread_count": notifications_data.get("unreadCount", 0),
            "health": health_data.get("status", "unknown"),
        }
