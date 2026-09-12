"""Tests for Cellarion setup, migration, and unload."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.cellarion.const import DOMAIN
from custom_components.cellarion.push import PUSH_FORBIDDEN_ISSUE, push_issue_id

from .conftest import BASE_URL, STATS_PAYLOAD, TEST_TOKEN, mock_cellarion_api


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


async def test_legacy_password_entry_needs_reauth(
    hass: HomeAssistant, aioclient_mock, password_entry
) -> None:
    """An entry that still holds a password is retired into the reauth flow."""
    password_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)

    assert not await hass.config_entries.async_setup(password_entry.entry_id)
    await hass.async_block_till_done()

    assert password_entry.state is ConfigEntryState.SETUP_ERROR
    assert password_entry.error_reason_translation_key == "password_entry_retired"
    # No request was made with the stored password
    assert not aioclient_mock.mock_calls
    assert any(
        flow["handler"] == DOMAIN and flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_server_error_at_setup_retries_later(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """A 5xx during the first refresh is a retry, not an auth failure."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock, stats_status=503)

    assert not await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()
    assert token_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()


async def test_options_change_reloads_entry(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """Saving new options reloads the entry with the new interval."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()
    first = token_entry.runtime_data

    hass.config_entries.async_update_entry(token_entry, options={"scan_interval": 900})
    await hass.async_block_till_done()

    assert token_entry.state is ConfigEntryState.LOADED
    assert token_entry.runtime_data is not first
    assert token_entry.runtime_data.update_interval.total_seconds() == 900


async def test_remove_entry_clears_push_issue(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """Deleting the integration removes its repair issue."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()
    ir.async_create_issue(
        hass,
        DOMAIN,
        push_issue_id(token_entry.entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=PUSH_FORBIDDEN_ISSUE,
    )

    await hass.config_entries.async_remove(token_entry.entry_id)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, push_issue_id(token_entry.entry_id)) is None


class _BrokenResources:
    loaded = False

    async def async_load(self) -> None:
        raise RuntimeError("storage exploded")


async def test_card_registration_failure_is_only_a_warning(
    hass: HomeAssistant, aioclient_mock, token_entry, caplog
) -> None:
    hass.data["lovelace"] = _lovelace(_BrokenResources())
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)

    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()
    assert token_entry.state is ConfigEntryState.LOADED
    assert "Could not register the Cellarion card automatically" in caplog.text


async def test_remove_entry_revokes_its_token(
    hass: HomeAssistant, aioclient_mock, token_entry, caplog
) -> None:
    """Deleting the integration asks the server to revoke the entry's token."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    aioclient_mock.delete(f"{BASE_URL}/api/tokens/self", json={"message": "Token revoked"})
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_remove(token_entry.entry_id)
    await hass.async_block_till_done()

    revokes = [c for c in aioclient_mock.mock_calls if c[0] == "DELETE"]
    assert len(revokes) == 1
    assert str(revokes[0][1]).endswith("/api/tokens/self")
    assert revokes[0][3]["Authorization"] == f"Bearer {TEST_TOKEN}"
    assert "Revoked the Cellarion API token" in caplog.text


async def test_remove_entry_survives_an_old_server(
    hass: HomeAssistant, aioclient_mock, token_entry, caplog
) -> None:
    """A server without the self-revoke route only earns a warning."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    aioclient_mock.delete(f"{BASE_URL}/api/tokens/self", status=403, json={"error": "scope"})
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_remove(token_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.config_entries.async_entries(DOMAIN)
    assert "could not be revoked automatically" in caplog.text
