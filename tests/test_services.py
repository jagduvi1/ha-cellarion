"""Tests for the cellarion.consume_bottle service."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.setup import async_setup_component

from custom_components.cellarion.const import DOMAIN

from .conftest import BASE_URL, mock_cellarion_api


async def test_consume_bottle(hass: HomeAssistant, aioclient_mock, token_entry) -> None:
    """The service posts to the consume endpoint and refreshes."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    aioclient_mock.post(
        f"{BASE_URL}/api/bottles/BOTTLE1/consume", json={"bottle": {}}
    )
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "consume_bottle",
        {"bottle_id": "BOTTLE1", "reason": "gifted", "rating": 4.5, "note": "hi"},
        blocking=True,
    )

    consume_calls = [
        call
        for call in aioclient_mock.mock_calls
        if str(call[1]).endswith("/api/bottles/BOTTLE1/consume")
    ]
    assert len(consume_calls) == 1
    assert consume_calls[0][2] == {
        "reason": "gifted",
        "rating": 4.5,
        "note": "hi",
    }


async def test_consume_bottle_accepts_201(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """A 201 Created (resource-creating POST) is treated as success."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    aioclient_mock.post(
        f"{BASE_URL}/api/bottles/BOTTLE1/consume",
        status=201,
        json={"bottle": {}},
    )
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    # Must not raise despite the non-200 success status
    await hass.services.async_call(
        DOMAIN, "consume_bottle", {"bottle_id": "BOTTLE1"}, blocking=True
    )


async def test_consume_without_entries(hass: HomeAssistant) -> None:
    """Calling the service with no accounts raises a validation error."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "consume_bottle", {"bottle_id": "X"}, blocking=True
        )


async def test_consume_unknown_entry_id(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """An unknown entry_id raises a validation error."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "consume_bottle",
            {"bottle_id": "X", "entry_id": "nope"},
            blocking=True,
        )


async def test_consume_api_error(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """A server error surfaces as HomeAssistantError."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    aioclient_mock.post(
        f"{BASE_URL}/api/bottles/BOTTLE1/consume",
        status=500,
        json={"error": "boom"},
    )
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "consume_bottle", {"bottle_id": "BOTTLE1"}, blocking=True
        )
