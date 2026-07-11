"""Tests for Cellarion sensors."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.cellarion.coordinator import _parse_peak_bottles
from custom_components.cellarion.sensor import SENSOR_DESCRIPTIONS

from .conftest import BASE_URL, mock_cellarion_api


def _desc(key: str):
    return next(d for d in SENSOR_DESCRIPTIONS if d.key == key)


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


def test_intake_per_year_handles_null_pace() -> None:
    """A null/absent avgIntakePerYear yields None instead of raising."""
    desc = _desc("intake_per_year")
    assert desc.value_fn({"pace": {"avgIntakePerYear": None}}) is None
    assert desc.value_fn({"pace": {}}) is None
    assert desc.value_fn({"pace": {"avgIntakePerYear": 24.25}}) == 24.2


def test_extra_attrs_tolerate_missing_keys() -> None:
    """Attribute builders don't KeyError on list elements missing keys."""
    countries = _desc("countries").extra_attrs_fn(
        {"by_country": [{"name": "Italy"}, {"count": 3}]}
    )
    assert countries["top_countries"] == [
        {"name": "Italy", "count": 0},
        {"name": None, "count": 3},
    ]
    producers = _desc("top_producers").extra_attrs_fn({"top_producers": [{}]})
    assert producers["producers"] == [{"name": None, "count": 0}]


def test_parse_peak_bottles_sorts_and_defaults() -> None:
    """Soonest-closing window first; None windows sort last; safe defaults."""
    out = _parse_peak_bottles(
        {
            "bottles": {
                "items": [
                    {"_id": "A", "drinkTo": 2030, "wineDefinition": {"name": "N"}},
                    {"_id": "B", "drinkTo": 2025},
                    {"_id": "C", "drinkTo": None},
                ]
            }
        }
    )
    assert [b["id"] for b in out] == ["B", "A", "C"]
    assert out[0]["producer"] == ""
    assert out[2]["vintage"] == "NV"
    assert out[1]["name"] == "N"


async def test_peak_bottles_api_failure_isolated(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """A peak-bottles API error leaves the other sensors working."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock, peak_status=500)
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.cellarion_total_bottles").state == "42"
    peak = hass.states.get("sensor.cellarion_bottles_at_peak")
    assert peak.attributes["peak_bottles"] == []


async def test_peak_bottles_malformed_payload_isolated(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """A malformed peak payload can't take the other sensors down."""
    token_entry.add_to_hass(hass)
    # bottles as a list rather than the expected dict
    mock_cellarion_api(aioclient_mock, peak_json={"bottles": ["oops"]})
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.cellarion_total_bottles").state == "42"
    peak = hass.states.get("sensor.cellarion_bottles_at_peak")
    assert peak.attributes["peak_bottles"] == []
