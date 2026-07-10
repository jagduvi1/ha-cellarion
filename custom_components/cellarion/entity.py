"""Base entity for Cellarion."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CellarionCoordinator


class CellarionEntity(CoordinatorEntity[CellarionCoordinator]):
    """Common device wiring for Cellarion entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CellarionCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Cellarion",
            manufacturer="Cellarion",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=coordinator.url,
        )
