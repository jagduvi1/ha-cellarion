"""Tests for Cellarion sensors."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import mock_cellarion_api


async def _setup(hass: HomeAssistant, aioclient_mock, entry) -> None:
    entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_sensor_values(hass: HomeAssistant, aioclient_mock, token_entry) -> None:
    """Core sensors expose the mapped API values."""
    await _setup(hass, aioclient_mock, token_entry)

    assert hass.states.get("sensor.cellarion_total_bottles").state == "42"
    value = hass.states.get("sensor.cellarion_collection_value")
    assert value.state == "1234.5"
    assert value.attributes["unit_of_measurement"] == "EUR"
    assert hass.states.get("sensor.cellarion_bottles_at_peak").state == "12"
    assert hass.states.get("sensor.cellarion_unread_notifications").state == "2"
    assert hass.states.get("sensor.cellarion_service_status").state == "ok"
    health = hass.states.get("sensor.cellarion_collection_health_score")
    assert health.state == "87"
    assert health.attributes["grade"] == "B+"


async def test_peak_bottles_attribute_sorted(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """The ready-to-drink list is sorted by soonest-closing window."""
    await _setup(hass, aioclient_mock, token_entry)

    peak = hass.states.get("sensor.cellarion_bottles_at_peak")
    bottles = peak.attributes["peak_bottles"]
    assert [b["id"] for b in bottles] == ["PEAK-SOON", "PEAK-LATER"]
    assert bottles[0]["drink_to"] == 2027


async def test_urgent_bottles_attribute(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """Urgent bottles carry ids for the consume service."""
    await _setup(hass, aioclient_mock, token_entry)

    declining = hass.states.get("sensor.cellarion_bottles_declining")
    assert declining.state == "3"
    assert declining.attributes["urgent_bottles"][0] == {
        "id": "URGENT1",
        "name": "Old Barolo",
        "vintage": 2005,
        "status": "declining",
    }


async def test_secondary_sensors_disabled_by_default(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """Less-popular sensors are registered but disabled by default."""
    await _setup(hass, aioclient_mock, token_entry)
    registry = er.async_get(hass)

    for suffix in (
        "countries",
        "oldest_vintage",
        "newest_vintage",
        "wine_types",
        "top_producers",
    ):
        entity_id = f"sensor.cellarion_{suffix}"
        assert hass.states.get(entity_id) is None
        reg_entry = registry.async_get(entity_id)
        assert reg_entry is not None
        assert reg_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
