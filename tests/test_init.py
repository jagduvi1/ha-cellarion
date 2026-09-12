"""Tests for Cellarion setup, migration, and unload."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cellarion.const import DOMAIN
from custom_components.cellarion.push import PUSH_FORBIDDEN_ISSUE, push_issue_id

from .conftest import BASE_URL, NEW_TOKEN, STATS_PAYLOAD, mock_cellarion_api


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


async def test_migration_keeps_account_id(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """The token rewrite must carry the account id the reauth guard relies on."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Cellarion (user@example.com)",
        unique_id=f"{BASE_URL}_user@example.com",
        data={
            "url": BASE_URL,
            "email": "user@example.com",
            "password": "hunter2",
            "account_id": "ACCOUNT-A",
        },
        options={"scan_interval": 1800},
    )
    entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.data["token"] == NEW_TOKEN
    assert entry.data["account_id"] == "ACCOUNT-A"


async def test_unload_clears_push_issue(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """Unloading removes the entry's own push repair issue, nobody else's."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    for entry_id in (token_entry.entry_id, "other-entry"):
        ir.async_create_issue(
            hass,
            DOMAIN,
            push_issue_id(entry_id),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=PUSH_FORBIDDEN_ISSUE,
        )

    assert await hass.config_entries.async_unload(token_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get_issue(DOMAIN, push_issue_id(token_entry.entry_id)) is None
    assert registry.async_get_issue(DOMAIN, push_issue_id("other-entry")) is not None


async def test_null_payload_sections_do_not_break_setup(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """A server answering with null blocks degrades to defaults, not errors."""
    token_entry.add_to_hass(hass)
    broken = {
        "stats": {
            **STATS_PAYLOAD["stats"],
            "overview": None,
            "maturity": None,
            "pace": None,
            "byType": None,
        }
    }
    aioclient_mock.get(f"{BASE_URL}/api/stats/overview", json=broken)
    aioclient_mock.get(f"{BASE_URL}/api/cellars", json={"count": None, "cellars": None})
    aioclient_mock.get(
        f"{BASE_URL}/api/notifications",
        json={"notifications": None, "unreadCount": None},
    )
    aioclient_mock.get(
        f"{BASE_URL}/api/health",
        text="<html>not json</html>",
        headers={"Content-Type": "text/html"},
    )
    aioclient_mock.get(f"{BASE_URL}/api/bottles", json={"bottles": None})
    aioclient_mock.get(f"{BASE_URL}/api/events/stream", status=404)

    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    assert token_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.cellarion_total_bottles").state == "0"
    assert hass.states.get("sensor.cellarion_bottles_at_peak").state == "0"
    assert hass.states.get("sensor.cellarion_service_status").state == "unreachable"
    assert hass.states.get("sensor.cellarion_unread_notifications").state == "0"
