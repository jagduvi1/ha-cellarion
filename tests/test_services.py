"""Tests for the cellarion.consume_bottle service."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.cellarion.const import DOMAIN

from .conftest import BASE_URL, NEW_TOKEN, mock_cellarion_api

# Cellarion bottle ids are MongoDB ObjectIds (24 hex chars)
BOTTLE1 = "6a50805b785f507654afdc51"


async def test_consume_bottle(hass: HomeAssistant, aioclient_mock, token_entry) -> None:
    """The service posts to the consume endpoint and refreshes."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    aioclient_mock.post(f"{BASE_URL}/api/bottles/{BOTTLE1}/consume", json={"bottle": {}})
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "consume_bottle",
        {"bottle_id": BOTTLE1, "reason": "gifted", "rating": 4.5, "note": "hi"},
        blocking=True,
    )

    consume_calls = [
        call
        for call in aioclient_mock.mock_calls
        if str(call[1]).endswith(f"/api/bottles/{BOTTLE1}/consume")
    ]
    assert len(consume_calls) == 1
    assert consume_calls[0][2] == {
        "reason": "gifted",
        "rating": 4.5,
        "note": "hi",
    }


async def test_consume_bottle_accepts_201(hass: HomeAssistant, aioclient_mock, token_entry) -> None:
    """A 201 Created (resource-creating POST) is treated as success."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    aioclient_mock.post(
        f"{BASE_URL}/api/bottles/{BOTTLE1}/consume",
        status=201,
        json={"bottle": {}},
    )
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    # Must not raise despite the non-200 success status
    await hass.services.async_call(DOMAIN, "consume_bottle", {"bottle_id": BOTTLE1}, blocking=True)


async def test_consume_without_entries(hass: HomeAssistant) -> None:
    """Calling the service with no accounts raises a validation error."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "consume_bottle", {"bottle_id": BOTTLE1}, blocking=True
        )


async def test_consume_unknown_entry_id(hass: HomeAssistant, aioclient_mock, token_entry) -> None:
    """An unknown entry_id raises a validation error."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "consume_bottle",
            {"bottle_id": BOTTLE1, "entry_id": "nope"},
            blocking=True,
        )


async def test_consume_api_error(hass: HomeAssistant, aioclient_mock, token_entry) -> None:
    """A server error surfaces as HomeAssistantError."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    aioclient_mock.post(
        f"{BASE_URL}/api/bottles/{BOTTLE1}/consume",
        status=500,
        json={"error": "boom"},
    )
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "consume_bottle", {"bottle_id": BOTTLE1}, blocking=True
        )


@pytest.mark.parametrize(
    "bad_id",
    ["BOTTLE1", "../../auth/whoami", "6a50805b785f507654afdc51#", "", "x" * 24],
)
async def test_consume_rejects_malformed_bottle_id(
    hass: HomeAssistant, aioclient_mock, token_entry, bad_id
) -> None:
    """Only a 24-hex ObjectId is accepted — the id goes into a URL path."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "consume_bottle", {"bottle_id": bad_id}, blocking=True
        )
    assert not any("/consume" in str(call[1]) for call in aioclient_mock.mock_calls)


async def test_consume_scope_error_is_validation_error(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """A token without the consume scope gets a specific, actionable error."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    aioclient_mock.post(
        f"{BASE_URL}/api/bottles/{BOTTLE1}/consume",
        status=403,
        json={"error": "scope"},
    )
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as excinfo:
        await hass.services.async_call(
            DOMAIN, "consume_bottle", {"bottle_id": BOTTLE1}, blocking=True
        )
    assert excinfo.value.translation_key == "consume_scope_missing"
    # No reauth: the token is valid, it just needs a different scope
    assert not hass.config_entries.flow.async_progress()


async def test_consume_auth_error_starts_reauth(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """A revoked token during consume raises and opens the reauth flow."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    aioclient_mock.post(
        f"{BASE_URL}/api/bottles/{BOTTLE1}/consume",
        status=401,
        json={"error": "revoked"},
    )
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError) as excinfo:
        await hass.services.async_call(
            DOMAIN, "consume_bottle", {"bottle_id": BOTTLE1}, blocking=True
        )
    assert excinfo.value.translation_key == "consume_auth_failed"
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(
        flow["handler"] == DOMAIN and flow["context"]["source"] == "reauth" for flow in flows
    )


async def _second_entry(hass: HomeAssistant, aioclient_mock) -> MockConfigEntry:
    other = MockConfigEntry(
        domain=DOMAIN,
        title="Cellarion (other.local)",
        unique_id="http://other.local_ACCOUNT-B",
        data={"url": "http://other.local", "token": NEW_TOKEN},
        options={"scan_interval": 1800},
    )
    other.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock, url="http://other.local")
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()
    return other


async def test_consume_with_two_accounts_needs_entry_id(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """Two loaded accounts: no entry_id is an error, entry_id routes the call."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()
    other = await _second_entry(hass, aioclient_mock)
    aioclient_mock.post(f"http://other.local/api/bottles/{BOTTLE1}/consume", json={})

    with pytest.raises(ServiceValidationError) as excinfo:
        await hass.services.async_call(
            DOMAIN, "consume_bottle", {"bottle_id": BOTTLE1}, blocking=True
        )
    assert excinfo.value.translation_key == "multiple_accounts"

    await hass.services.async_call(
        DOMAIN,
        "consume_bottle",
        {"bottle_id": BOTTLE1, "entry_id": other.entry_id},
        blocking=True,
    )
    consume_calls = [c for c in aioclient_mock.mock_calls if "/consume" in str(c[1])]
    assert len(consume_calls) == 1
    assert str(consume_calls[0][1]).startswith("http://other.local/")


async def test_consume_ignores_entries_that_are_not_loaded(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """An unloaded second entry doesn't make the single-account case ambiguous."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()
    other = await _second_entry(hass, aioclient_mock)
    await hass.config_entries.async_unload(other.entry_id)
    await hass.async_block_till_done()
    aioclient_mock.post(f"{BASE_URL}/api/bottles/{BOTTLE1}/consume", json={})

    await hass.services.async_call(DOMAIN, "consume_bottle", {"bottle_id": BOTTLE1}, blocking=True)
