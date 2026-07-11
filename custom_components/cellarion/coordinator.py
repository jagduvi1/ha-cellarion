"""Data update coordinator for Cellarion."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    CellarionApiClient,
    CellarionApiError,
    CellarionAuthError,
    CellarionScopeError,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _parse_peak_bottles(peak_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Shape the peak-bottle payload into the sensor's attribute list.

    Kept small and pure so the caller can treat any error (missing keys,
    unexpected types) as "no peak data" without failing the whole update.
    """
    peak_items = peak_data.get("bottles", {}).get("items", [])
    return sorted(
        (
            {
                "id": b.get("_id"),
                "name": (b.get("wineDefinition") or {}).get("name", "Unknown"),
                "producer": (b.get("wineDefinition") or {}).get("producer", ""),
                "vintage": b.get("vintage") or "NV",
                "drink_to": b.get("drinkTo"),
            }
            for b in peak_items
        ),
        # Drink first what leaves its window first; None (unknown) sorts last
        key=lambda x: (x["drink_to"] is None, x["drink_to"]),
    )


class CellarionCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls the Cellarion API."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: CellarionApiClient,
        scan_interval: int,
        url: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
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
            "instance_url": self.url,
            "peak_bottles": peak_bottles,
        }
