"""Snapshot tests: the full state of every sensor, as core reviewers expect."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from syrupy.assertion import SnapshotAssertion

from custom_components.cellarion.sensor import SENSOR_DESCRIPTIONS

from .conftest import mock_cellarion_api


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_sensor_snapshots(
    hass: HomeAssistant,
    aioclient_mock,
    entity_registry: er.EntityRegistry,
    token_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Every sensor's state, attributes and registry entry match the snapshot."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    entries = er.async_entries_for_config_entry(entity_registry, token_entry.entry_id)
    assert len(entries) == len(SENSOR_DESCRIPTIONS)
    for entry in sorted(entries, key=lambda e: e.entity_id):
        state = hass.states.get(entry.entity_id)
        assert state, entry.entity_id
        assert state == snapshot(name=f"{entry.entity_id}-state")
        # The registry entry itself gains fields between HA releases; the
        # parts the integration controls are what must stay stable.
        assert {
            "unique_id": entry.unique_id,
            "translation_key": entry.translation_key,
            "original_name": entry.original_name,
            "device_class": entry.device_class,
            "original_device_class": entry.original_device_class,
            "entity_category": entry.entity_category,
            "disabled_by": entry.disabled_by,
            "unit_of_measurement": entry.unit_of_measurement,
            "capabilities": entry.capabilities,
            "has_entity_name": entry.has_entity_name,
        } == snapshot(name=f"{entry.entity_id}-entry")
