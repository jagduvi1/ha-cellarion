"""Tests for the Cellarion config flow."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cellarion.const import DOMAIN

from .conftest import (
    BASE_URL,
    NEW_TOKEN,
    TEST_TOKEN,
    mock_cellarion_api,
)


@pytest.fixture(autouse=True)
def no_setup():
    """Config-flow tests never run full entry setup."""
    with patch(
        "custom_components.cellarion.async_setup_entry", return_value=True
    ):
        yield


async def _menu_to(hass: HomeAssistant, step: str, context=None, data=None):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context=context or {"source": SOURCE_USER}, data=data
    )
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_user_menu(hass: HomeAssistant) -> None:
    """The initial step is a token/password menu."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"token", "password"}


async def test_token_flow_creates_entry(hass: HomeAssistant, aioclient_mock) -> None:
    """Pasting a valid token creates a token-only entry."""
    mock_cellarion_api(aioclient_mock)
    result = await _menu_to(hass, "token")
    assert result["step_id"] == "token"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": BASE_URL, "token": TEST_TOKEN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"url": BASE_URL, "token": TEST_TOKEN}
    assert "password" not in result["data"]


@pytest.mark.parametrize(
    ("stats_status", "expected_error"),
    [(401, "invalid_token"), (403, "token_scope"), (500, "cannot_connect")],
)
async def test_token_flow_errors(
    hass: HomeAssistant, aioclient_mock, stats_status, expected_error
) -> None:
    """Token validation errors map to the right error keys."""
    mock_cellarion_api(aioclient_mock, stats_status=stats_status)
    result = await _menu_to(hass, "token")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": BASE_URL, "token": TEST_TOKEN}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}


async def test_token_flow_cannot_connect(hass: HomeAssistant, aioclient_mock) -> None:
    """A network error maps to cannot_connect."""
    aioclient_mock.get(
        f"{BASE_URL}/api/stats/overview", exc=aiohttp.ClientError("boom")
    )
    result = await _menu_to(hass, "token")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": BASE_URL, "token": TEST_TOKEN}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_password_flow_mints_token(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """Email+password mints a scoped token and stores no password."""
    mock_cellarion_api(aioclient_mock)
    result = await _menu_to(hass, "password")
    assert result["step_id"] == "password"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"url": BASE_URL, "email": "user@example.com", "password": "pw"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "url": BASE_URL,
        "email": "user@example.com",
        "token": NEW_TOKEN,
    }
    # The mint request carried the right scopes
    mint_call = [
        c for c in aioclient_mock.mock_calls if str(c[1]).endswith("/api/tokens")
    ][0]
    assert mint_call[2]["scopes"] == ["read", "consume"]


async def test_password_flow_old_server_stores_password(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """Servers without /api/tokens fall back to password storage."""
    mock_cellarion_api(aioclient_mock, tokens_status=404)
    result = await _menu_to(hass, "password")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"url": BASE_URL, "email": "user@example.com", "password": "pw"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["password"] == "pw"
    assert "token" not in result["data"]


async def test_password_flow_invalid_auth(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """A rejected login shows invalid_auth."""
    mock_cellarion_api(aioclient_mock, login_status=401)
    result = await _menu_to(hass, "password")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"url": BASE_URL, "email": "user@example.com", "password": "bad"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_password_flow_duplicate_aborts(
    hass: HomeAssistant, aioclient_mock, password_entry
) -> None:
    """The same URL+email cannot be added twice."""
    password_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    result = await _menu_to(hass, "password")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"url": BASE_URL, "email": "user@example.com", "password": "pw"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_token(hass: HomeAssistant, aioclient_mock, token_entry) -> None:
    """Reauth via token keeps the URL and swaps the token."""
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)

    result = await _menu_to(
        hass,
        "token",
        context={"source": SOURCE_REAUTH, "entry_id": token_entry.entry_id},
        data=token_entry.data,
    )
    # Reauth locks the URL — only the token field is asked
    schema_keys = [k.schema for k in result["data_schema"].schema]
    assert schema_keys == ["token"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"token": NEW_TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert token_entry.data["token"] == NEW_TOKEN


async def test_reconfigure_token_changes_url(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """Reconfigure allows changing the URL."""
    new_url = "http://other.local"
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    mock_cellarion_api(aioclient_mock, url=new_url)

    result = await _menu_to(
        hass,
        "token",
        context={
            "source": SOURCE_RECONFIGURE,
            "entry_id": token_entry.entry_id,
        },
    )
    schema_keys = [k.schema for k in result["data_schema"].schema]
    assert schema_keys == ["url", "token"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": new_url, "token": NEW_TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert token_entry.data["url"] == new_url
    assert token_entry.data["token"] == NEW_TOKEN
