"""Tests for the Cellarion API client."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.cellarion.api import (
    CellarionApiClient,
    CellarionAuthError,
    CellarionScopeError,
    CellarionTokensNotSupported,
)

from .conftest import BASE_URL, JWT, TEST_TOKEN


async def test_login_sends_username_field(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """The login payload uses 'username' (the field the API expects)."""
    aioclient_mock.post(f"{BASE_URL}/api/auth/login", json={"token": JWT})
    client = CellarionApiClient(
        async_get_clientsession(hass), BASE_URL, "user@example.com", "pw"
    )
    assert await client.authenticate()
    assert aioclient_mock.mock_calls[0][2] == {
        "username": "user@example.com",
        "password": "pw",
    }


async def test_invalid_login_raises_auth_error(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """400/401 logins raise CellarionAuthError."""
    aioclient_mock.post(
        f"{BASE_URL}/api/auth/login", status=401, json={"error": "no"}
    )
    client = CellarionApiClient(
        async_get_clientsession(hass), BASE_URL, "user@example.com", "bad"
    )
    with pytest.raises(CellarionAuthError):
        await client.authenticate()


async def test_static_token_never_relogs(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """A 401 with an API token raises immediately (revoked token)."""
    aioclient_mock.get(
        f"{BASE_URL}/api/stats/overview", status=401, json={"error": "no"}
    )
    client = CellarionApiClient(
        async_get_clientsession(hass), BASE_URL, token=TEST_TOKEN
    )
    with pytest.raises(CellarionAuthError):
        await client.get_stats_overview()
    # Exactly one request — no login retry loop
    assert len(aioclient_mock.mock_calls) == 1


async def test_token_scope_error(hass: HomeAssistant, aioclient_mock) -> None:
    """A 403 with an API token raises CellarionScopeError."""
    aioclient_mock.get(
        f"{BASE_URL}/api/stats/overview", status=403, json={"error": "scope"}
    )
    client = CellarionApiClient(
        async_get_clientsession(hass), BASE_URL, token=TEST_TOKEN
    )
    with pytest.raises(CellarionScopeError):
        await client.get_stats_overview()


async def test_create_token_not_supported(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """404 on /api/tokens raises CellarionTokensNotSupported."""
    aioclient_mock.post(f"{BASE_URL}/api/auth/login", json={"token": JWT})
    aioclient_mock.post(
        f"{BASE_URL}/api/tokens", status=404, json={"error": "nope"}
    )
    client = CellarionApiClient(
        async_get_clientsession(hass), BASE_URL, "user@example.com", "pw"
    )
    with pytest.raises(CellarionTokensNotSupported):
        await client.async_create_api_token("HA", ["read"])
