"""Sensor platform for Cellarion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import CellarionCoordinator
from .entity import CellarionEntity

if TYPE_CHECKING:
    from . import CellarionConfigEntry

# Read-only coordinator entities — no request throttling needed
PARALLEL_UPDATES = 0


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
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _get_overview(d, "totalBottles", 0),
    ),
    CellarionSensorDescription(
        key="collection_value",
        translation_key="collection_value",
        # MONETARY only allows TOTAL — MEASUREMENT logs a validation error
        state_class=SensorStateClass.TOTAL,
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
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _get_overview(d, "uniqueWines", 0),
    ),
    CellarionSensorDescription(
        key="cellar_count",
        translation_key="cellar_count",
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
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: _get_overview(d, "totalCountries", 0),
        extra_attrs_fn=lambda d: {
            "top_countries": [
                {"name": c.get("name"), "count": c.get("count", 0)}
                for c in d.get("by_country", [])[:5]
            ]
        },
    ),
    # ── Maturity / Drink window ──────────────────────────────────────
    CellarionSensorDescription(
        key="bottles_at_peak",
        translation_key="bottles_at_peak",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("maturity", {}).get("peak", 0),
        extra_attrs_fn=lambda d: {
            "peak_bottles": d.get("peak_bottles", []),
        },
    ),
    CellarionSensorDescription(
        key="bottles_declining",
        translation_key="bottles_declining",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("maturity", {}).get("declining", 0),
        extra_attrs_fn=lambda d: {
            "urgent_bottles": [
                {
                    # id is present on servers that include it in the
                    # urgency ladder; enables consume-from-card and the
                    # cellarion.consume_bottle service in automations
                    "id": b.get("id"),
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
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("maturity", {}).get("notReady", 0),
    ),
    CellarionSensorDescription(
        key="bottles_early",
        translation_key="bottles_early",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("maturity", {}).get("early", 0),
    ),
    CellarionSensorDescription(
        key="bottles_late",
        translation_key="bottles_late",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("maturity", {}).get("late", 0),
    ),
    # ── Consumption & pace ───────────────────────────────────────────
    CellarionSensorDescription(
        key="consumed_bottles",
        translation_key="consumed_bottles",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _get_overview(d, "totalConsumed", 0),
    ),
    CellarionSensorDescription(
        key="intake_per_year",
        translation_key="intake_per_year",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (
            round(v, 1)
            if (v := d.get("pace", {}).get("avgIntakePerYear")) is not None
            else None
        ),
    ),
    CellarionSensorDescription(
        key="runway_years",
        translation_key="runway_years",
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
        entity_registry_enabled_default=False,
        value_fn=lambda d: _get_overview(d, "oldestVintage"),
    ),
    CellarionSensorDescription(
        key="newest_vintage",
        translation_key="newest_vintage",
        entity_registry_enabled_default=False,
        value_fn=lambda d: _get_overview(d, "newestVintage"),
    ),
    # ── Health score ─────────────────────────────────────────────────
    CellarionSensorDescription(
        key="health_score",
        translation_key="health_score",
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
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("unread_count", 0),
    ),
    # ── Wine types breakdown ─────────────────────────────────────────
    CellarionSensorDescription(
        key="wine_types",
        translation_key="wine_types",
        entity_registry_enabled_default=False,
        value_fn=lambda d: len(d.get("by_type", {})),
        extra_attrs_fn=lambda d: d.get("by_type", {}),
    ),
    # ── Top producers ────────────────────────────────────────────────
    CellarionSensorDescription(
        key="top_producers",
        translation_key="top_producers",
        entity_registry_enabled_default=False,
        value_fn=lambda d: len(d.get("top_producers", [])),
        extra_attrs_fn=lambda d: {
            "producers": [
                {"name": p.get("name"), "count": p.get("count", 0)}
                for p in d.get("top_producers", [])[:10]
            ]
        },
    ),
    # ── Service health ───────────────────────────────────────────────
    CellarionSensorDescription(
        key="service_health",
        translation_key="service_health",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("health", "unknown"),
        extra_attrs_fn=lambda d: {
            "instance_url": d.get("instance_url"),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CellarionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Cellarion sensors from a config entry."""
    coordinator = entry.runtime_data

    async_add_entities(
        CellarionSensor(coordinator, description, entry.entry_id)
        for description in SENSOR_DESCRIPTIONS
    )


class CellarionSensor(CellarionEntity, SensorEntity):
    """Representation of a Cellarion sensor."""

    entity_description: CellarionSensorDescription

    def __init__(
        self,
        coordinator: CellarionCoordinator,
        description: CellarionSensorDescription,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator, entry_id)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"

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
