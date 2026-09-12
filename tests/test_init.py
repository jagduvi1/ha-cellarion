"""Tests for Cellarion setup, migration, and unload."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cellarion.const import DOMAIN
from custom_components.cellarion.push import PUSH_FORBIDDEN_ISSUE, push_issue_id

from .conftest import BASE_URL, NEW_TOKEN, STATS_PAYLOAD, mock_cellarion_api


async def test_setup_token_entry(hass: HomeAssistant, aioclient_mock, token_entry) -> None:
    """A token entry sets up, exposes runtime data, and unloads."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)

    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    assert token_entry.state is ConfigEntryState.LOADED
    coordinator = token_entry.runtime_data
    assert coordinator.data["overview"]["totalBottles"] == 42
    # No login happened — token auth goes straight to the API
    assert not any(str(call[1]).endswith("/api/auth/login") for call in aioclient_mock.mock_calls)

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
        flow["handler"] == DOMAIN and flow["context"]["source"] == "reauth" for flow in flows
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


async def test_migration_keeps_account_id(hass: HomeAssistant, aioclient_mock) -> None:
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


async def test_unload_clears_push_issue(hass: HomeAssistant, aioclient_mock, token_entry) -> None:
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


async def test_migration_postponed_when_server_errors(
    hass: HomeAssistant, aioclient_mock, password_entry
) -> None:
    """A transient error minting the token keeps password auth and still loads."""
    password_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock, tokens_status=500)

    assert await hass.config_entries.async_setup(password_entry.entry_id)
    await hass.async_block_till_done()

    assert password_entry.state is ConfigEntryState.LOADED
    assert password_entry.data["password"] == "hunter2"
    assert "token" not in password_entry.data


async def test_missing_scope_at_setup_starts_reauth(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """A token without the read scope fails setup with the scope message."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock, stats_status=403)

    assert not await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    assert token_entry.state is ConfigEntryState.SETUP_ERROR
    assert token_entry.error_reason_translation_key == "scope_missing"
    assert any(
        flow["handler"] == DOMAIN and flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


class _FakeResources:
    """Stand-in for Lovelace's resource collection."""

    def __init__(self, items: list[dict] | None = None) -> None:
        self.loaded = False
        self._items = items or []
        self.created: list[dict] = []
        self.updated: list[tuple[str, dict]] = []

    async def async_load(self) -> None:
        self.loaded = True

    def async_items(self) -> list[dict]:
        return self._items

    async def async_create_item(self, data: dict) -> None:
        self.created.append(data)

    async def async_update_item(self, item_id: str, data: dict) -> None:
        self.updated.append((item_id, data))


class _YamlResources:
    """YAML dashboards expose a read-only collection: no create/update."""

    loaded = True

    def async_items(self) -> list[dict]:
        return []


def _lovelace(resources: _FakeResources) -> SimpleNamespace:
    return SimpleNamespace(resources=resources)


async def test_card_resource_is_created_once(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """First setup adds the versioned card resource; a reload doesn't add another."""
    resources = _FakeResources()
    hass.data["lovelace"] = _lovelace(resources)
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)

    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    assert resources.loaded
    assert len(resources.created) == 1
    assert resources.created[0]["res_type"] == "module"
    assert resources.created[0]["url"].startswith("/cellarion-files/cellarion-card.js?v=")

    await hass.config_entries.async_reload(token_entry.entry_id)
    await hass.async_block_till_done()
    assert len(resources.created) == 1


async def test_card_resource_is_reversioned_after_upgrade(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """An existing resource with an old version suffix is updated in place."""
    resources = _FakeResources(
        items=[{"id": "r1", "url": "/cellarion-files/cellarion-card.js?v=0.0.1"}]
    )
    hass.data["lovelace"] = _lovelace(resources)
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)

    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    assert resources.created == []
    assert len(resources.updated) == 1
    assert resources.updated[0][0] == "r1"
    assert "?v=0.0.1" not in resources.updated[0][1]["url"]


async def test_card_registration_never_blocks_setup(
    hass: HomeAssistant, aioclient_mock, token_entry, caplog
) -> None:
    """YAML-mode dashboards and a failing collection only log; setup succeeds."""
    hass.data["lovelace"] = _lovelace(_YamlResources())
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)

    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()
    assert token_entry.state is ConfigEntryState.LOADED
    assert "YAML mode" in caplog.text
