"""Shared fixtures and API mocks for Cellarion tests."""

from __future__ import annotations

import pytest

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cellarion.const import DOMAIN

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading the custom integration in every test."""
    yield


BASE_URL = "http://cellarion.local"
TEST_TOKEN = "cel_" + "a" * 64
NEW_TOKEN = "cel_" + "b" * 64
JWT = "jwt-access-token"

STATS_PAYLOAD = {
    "stats": {
        "overview": {
            "totalBottles": 42,
            "totalConsumed": 7,
            "uniqueWines": 30,
            "totalCountries": 5,
            "totalValue": 1234.5,
            "currency": "EUR",
            "avgPrice": 29.4,
            "avgRating": 4.25,
            "oldestVintage": 1999,
            "newestVintage": 2024,
            "healthScore": 87,
            "healthGrade": "B+",
        },
        "maturity": {
            "peak": 12,
            "declining": 3,
            "late": 1,
            "early": 6,
            "notReady": 20,
            "noProfile": 0,
        },
        "pace": {"avgIntakePerYear": 24.2, "runway": 6.4},
        "cellarBreakdown": [{"name": "Main", "bottleCount": 42}],
        "byType": {"red": 30, "white": 12},
        "byCountry": [{"name": "Italy", "count": 20, "code": "IT"}],
        "topProducers": [{"name": "Test Winery", "count": 9}],
        "urgencyLadder": [
            {
                "id": "URGENT1",
                "name": "Old Barolo",
                "vintage": 2005,
                "status": "declining",
            }
        ],
    }
}

PEAK_PAYLOAD = {
    "bottles": {
        "count": 2,
        "total": 12,
        "items": [
            {
                "_id": "PEAK-LATER",
                "vintage": 2019,
                "drinkFrom": 2022,
                "drinkTo": 2032,
                "wineDefinition": {"name": "Later Wine", "producer": "P1"},
            },
            {
                "_id": "PEAK-SOON",
                "vintage": 2018,
                "drinkFrom": 2022,
                "drinkTo": 2027,
                "wineDefinition": {"name": "Soon Wine", "producer": "P2"},
            },
        ],
    }
}


def mock_cellarion_api(
    aioclient_mock,
    url: str = BASE_URL,
    login_status: int = 200,
    tokens_status: int = 200,
    stats_status: int = 200,
    peak_status: int = 200,
    peak_json: dict | None = None,
) -> None:
    """Register happy-path (or overridden) mocks for the Cellarion API."""
    if login_status == 200:
        aioclient_mock.post(
            f"{url}/api/auth/login", json={"token": JWT, "user": {}}
        )
    else:
        aioclient_mock.post(
            f"{url}/api/auth/login",
            status=login_status,
            json={"error": "Invalid credentials"},
        )

    if tokens_status == 200:
        aioclient_mock.post(f"{url}/api/tokens", json={"token": NEW_TOKEN})
    else:
        aioclient_mock.post(
            f"{url}/api/tokens", status=tokens_status, json={"error": "nope"}
        )

    if stats_status == 200:
        aioclient_mock.get(f"{url}/api/stats/overview", json=STATS_PAYLOAD)
    else:
        aioclient_mock.get(
            f"{url}/api/stats/overview",
            status=stats_status,
            json={"error": "denied"},
        )

    aioclient_mock.get(
        f"{url}/api/cellars", json={"count": 1, "cellars": [{"name": "Main"}]}
    )
    aioclient_mock.get(
        f"{url}/api/notifications",
        json={"notifications": [], "unreadCount": 2},
    )
    aioclient_mock.get(
        f"{url}/api/health", json={"status": "ok", "version": "1.75.0"}
    )
    if peak_status == 200:
        aioclient_mock.get(
            f"{url}/api/bottles",
            json=peak_json if peak_json is not None else PEAK_PAYLOAD,
        )
    else:
        aioclient_mock.get(
            f"{url}/api/bottles", status=peak_status, json={"error": "no"}
        )
    # No push support in tests — the listener falls back to polling
    aioclient_mock.get(f"{url}/api/events/stream", status=404)


@pytest.fixture
def token_entry() -> MockConfigEntry:
    """A config entry authenticated with an API token."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Cellarion (cellarion.local)",
        unique_id=f"{BASE_URL}_token_abcdef123456",
        data={"url": BASE_URL, "token": TEST_TOKEN},
        options={"scan_interval": 1800},
    )


@pytest.fixture
def password_entry() -> MockConfigEntry:
    """A legacy config entry that still stores the account password."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Cellarion (user@example.com)",
        unique_id=f"{BASE_URL}_user@example.com",
        data={
            "url": BASE_URL,
            "email": "user@example.com",
            "password": "hunter2",
        },
        options={"scan_interval": 1800},
    )
