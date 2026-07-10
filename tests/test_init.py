"""Tests for Cellarion setup, migration, and unload."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.cellarion.const import DOMAIN

from .conftest import NEW_TOKEN, mock_cellarion_api


async def test_setup_token_entry(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """A token entry sets up, exposes runtime data, and unloads."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)

    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    assert token_entry.state is ConfigEntryState.LOADED
    coordinator = token_entry.runtime_data
    assert coordinator.data["overview"]["totalBottles"] == 42
    # No login happened — token auth goes straight to the API
    assert not any(
        str(call[1]).endswith("/api/auth/login")
        for call in aioclient_mock.mock_calls
    )

    assert await hass.config_entries.async_unload(token_entry.entry_id)
    await hass.async_block_till_done()
    assert token_entry.state is ConfigEntryState.NOT_LOADED


async def test_password_entry_migrates_to_token(
    hass: HomeAssistant, aioclient_mock, password_entry
) -> None:
    """Legacy password entries mint a token and drop the password."""
    password_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)

    assert await hass.config_entries.async_setup(password_entry.entry_id)
    await hass.async_block_till_done()

    assert password_entry.state is ConfigEntryState.LOADED
    assert password_entry.data["token"] == NEW_TOKEN
    assert "password" not in password_entry.data
    assert password_entry.data["email"] == "user@example.com"


async def test_password_entry_kept_on_old_server(
    hass: HomeAssistant, aioclient_mock, password_entry
) -> None:
    """Servers without token support keep password auth working."""
    password_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock, tokens_status=404)

    assert await hass.config_entries.async_setup(password_entry.entry_id)
    await hass.async_block_till_done()

    assert password_entry.state is ConfigEntryState.LOADED
    assert password_entry.data["password"] == "hunter2"
    assert "token" not in password_entry.data


async def test_bad_password_starts_reauth(
    hass: HomeAssistant, aioclient_mock, password_entry
) -> None:
    """A rejected stored password fails setup and opens a reauth flow."""
    password_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock, login_status=401)

    assert not await hass.config_entries.async_setup(password_entry.entry_id)
    await hass.async_block_till_done()

    assert password_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(
        flow["handler"] == DOMAIN
        and flow["context"]["source"] == "reauth"
        for flow in flows
    )


async def test_revoked_token_starts_reauth(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """A revoked API token fails setup and opens a reauth flow."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock, stats_status=401)

    assert not await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    assert token_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["handler"] == DOMAIN for flow in flows)
