"""Sensor platform for Cellarion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CellarionCoordinator


@dataclass(frozen=True, kw_only=True)
class CellarionSensorDescription(SensorEntityDescription):
    """Describe a Cellarion sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    extra_attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _get_overview(data: dict, key: str, default: Any = None) -> Any:
    return data.get("overview", {}).get(key, default)


SENSOR_DESCRIPTIONS: tuple[CellarionSensorDescription, ...] = (
    # ── Collection overview ──────────────────────────────────────────
    CellarionSensorDescription(
        key="total_bottles",
        translation_key="total_bottles",
        icon="mdi:bottle-wine",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _get_overview(d, "totalBottles", 0),
    ),
    CellarionSensorDescription(
        key="collection_value",
        translation_key="collection_value",
        icon="mdi:cash-multiple",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda d: _get_overview(d, "totalValue", 0),
        extra_attrs_fn=lambda d: {
            "currency": _get_overview(d, "currency", "EUR"),
            "average_price": _get_overview(d, "avgPrice", 0),
        },
    ),
    CellarionSensorDescription(
        key="unique_wines",
        translation_key="unique_wines",
        icon="mdi:glass-wine",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _get_overview(d, "uniqueWines", 0),
    ),
    CellarionSensorDescription(
        key="cellar_count",
        translation_key="cellar_count",
        icon="mdi:warehouse",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("cellar_count", 0),
        extra_attrs_fn=lambda d: {
            "cellars": [
                {"name": c.get("name"), "bottles": c.get("bottleCount", 0)}
                for c in d.get("cellar_breakdown", [])
            ]
        },
    ),
    CellarionSensorDescription(
        key="average_rating",
        translation_key="average_rating",
        icon="mdi:star",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (
            round(_get_overview(d, "avgRating"), 1)
            if _get_overview(d, "avgRating") is not None
            else None
        ),
    ),
    CellarionSensorDescription(
        key="countries",
        translation_key="countries",
        icon="mdi:earth",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _get_overview(d, "totalCountries", 0),
        extra_attrs_fn=lambda d: {
            "top_countries": [
                {"name": c["name"], "count": c["count"]}
                for c in d.get("by_country", [])[:5]
            ]
        },
    ),
    # ── Maturity / Drink window ──────────────────────────────────────
    CellarionSensorDescription(
        key="bottles_at_peak",
        translation_key="bottles_at_peak",
        icon="mdi:glass-cocktail",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("maturity", {}).get("peak", 0),
    ),
    CellarionSensorDescription(
        key="bottles_declining",
        translation_key="bottles_declining",
        icon="mdi:alert-circle",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("maturity", {}).get("declining", 0),
        extra_attrs_fn=lambda d: {
            "urgent_bottles": [
                {
                    "name": b.get("name"),
                    "vintage": b.get("vintage"),
                    "status": b.get("status"),
                }
                for b in d.get("urgency_ladder", [])[:10]
            ]
        },
    ),
    CellarionSensorDescription(
        key="bottles_not_ready",
        translation_key="bottles_not_ready",
        icon="mdi:timer-sand",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("maturity", {}).get("notReady", 0),
    ),
    CellarionSensorDescription(
        key="bottles_early",
        translation_key="bottles_early",
        icon="mdi:sprout",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("maturity", {}).get("early", 0),
    ),
    CellarionSensorDescription(
        key="bottles_late",
        translation_key="bottles_late",
        icon="mdi:clock-alert",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("maturity", {}).get("late", 0),
    ),
    # ── Consumption & pace ───────────────────────────────────────────
    CellarionSensorDescription(
        key="consumed_bottles",
        translation_key="consumed_bottles",
        icon="mdi:bottle-wine-outline",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _get_overview(d, "totalConsumed", 0),
    ),
    CellarionSensorDescription(
        key="intake_per_year",
        translation_key="intake_per_year",
        icon="mdi:trending-up",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (
            round(d.get("pace", {}).get("avgIntakePerYear", 0), 1)
        ),
    ),
    CellarionSensorDescription(
        key="runway_years",
        translation_key="runway_years",
        icon="mdi:road-variant",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (
            round(d.get("pace", {}).get("runway"), 1)
            if d.get("pace", {}).get("runway") is not None
            else None
        ),
    ),
    # ── Vintages ─────────────────────────────────────────────────────
    CellarionSensorDescription(
        key="oldest_vintage",
        translation_key="oldest_vintage",
        icon="mdi:calendar-arrow-left",
        value_fn=lambda d: _get_overview(d, "oldestVintage"),
    ),
    CellarionSensorDescription(
        key="newest_vintage",
        translation_key="newest_vintage",
        icon="mdi:calendar-arrow-right",
        value_fn=lambda d: _get_overview(d, "newestVintage"),
    ),
    # ── Health score ─────────────────────────────────────────────────
    CellarionSensorDescription(
        key="health_score",
        translation_key="health_score",
        icon="mdi:heart-pulse",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _get_overview(d, "healthScore"),
        extra_attrs_fn=lambda d: {
            "grade": _get_overview(d, "healthGrade"),
        },
    ),
    # ── Notifications ────────────────────────────────────────────────
    CellarionSensorDescription(
        key="unread_notifications",
        translation_key="unread_notifications",
        icon="mdi:bell",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("unread_count", 0),
    ),
    # ── Wine types breakdown ─────────────────────────────────────────
    CellarionSensorDescription(
        key="wine_types",
        translation_key="wine_types",
        icon="mdi:format-list-bulleted",
        value_fn=lambda d: len(d.get("by_type", {})),
        extra_attrs_fn=lambda d: d.get("by_type", {}),
    ),
    # ── Top producers ────────────────────────────────────────────────
    CellarionSensorDescription(
        key="top_producers",
        translation_key="top_producers",
        icon="mdi:domain",
        value_fn=lambda d: len(d.get("top_producers", [])),
        extra_attrs_fn=lambda d: {
            "producers": [
                {"name": p["name"], "count": p["count"]}
                for p in d.get("top_producers", [])[:10]
            ]
        },
    ),
    # ── Service health ───────────────────────────────────────────────
    CellarionSensorDescription(
        key="service_health",
        translation_key="service_health",
        icon="mdi:server",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("health", "unknown"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Cellarion sensors from a config entry."""
    coordinator: CellarionCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        CellarionSensor(coordinator, description, entry)
        for description in SENSOR_DESCRIPTIONS
    ]
    async_add_entities(entities)


class CellarionSensor(
    CoordinatorEntity[CellarionCoordinator], SensorEntity
):
    """Representation of a Cellarion sensor."""

    entity_description: CellarionSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CellarionCoordinator,
        description: CellarionSensorDescription,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Cellarion",
            manufacturer="Cellarion",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data.get("url", "https://cellarion.app"),
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit — use currency from API for monetary sensors."""
        if (
            self.entity_description.device_class
            == SensorDeviceClass.MONETARY
            and self.coordinator.data
        ):
            return (
                self.coordinator.data.get("overview", {})
                .get("currency", "EUR")
            )
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if (
            self.entity_description.extra_attrs_fn
            and self.coordinator.data
        ):
            return self.entity_description.extra_attrs_fn(
                self.coordinator.data
            )
        return None
