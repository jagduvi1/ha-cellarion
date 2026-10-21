"""Tests for the inventory: the get_bottles action, its cache, the image proxy."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pycellarion import CellarionApiError, InventoryResult
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.cellarion import inventory as inventory_module
from custom_components.cellarion.const import DOMAIN
from custom_components.cellarion.inventory import (
    INVENTORY_MIN_VERSION,
    CellarionInventory,
    filter_inventory,
)

from .conftest import BASE_URL, NEW_TOKEN, mock_cellarion_api

CELLAR_HOME = "c1" * 12
CELLAR_CABIN = "c2" * 12
RACK = "a1" * 12
WINE_RED = "e1" * 12
WINE_WHITE = "e2" * 12
B_RED = "b1" * 12
B_WHITE = "b2" * 12
B_PENDING = "b3" * 12
THUMB = "0f8fad5b-d9cb-469f-a165-70867728950e.webp.webp"

INVENTORY: dict[str, Any] = {
    "schema": 1,
    "generatedAt": "2026-10-21T12:00:00.000Z",
    "cellars": [
        {"id": CELLAR_HOME, "name": "Home", "role": "owner"},
        {"id": CELLAR_CABIN, "name": "Cabin", "role": "editor"},
    ],
    "racks": [{"id": RACK, "cellar": CELLAR_HOME, "name": "Left cabinet"}],
    "wines": {
        WINE_RED: {
            "name": "Côte-Rôtie La Landonne",
            "producer": "Guigal",
            "type": "red",
            "colour": None,
            "country": "France",
            "region": "Rhône",
            "appellation": "Côte-Rôtie",
            "classification": None,
            "grapes": ["Syrah"],
            "image": f"/api/uploads/thumbs/processed/{THUMB}",
            "profile": None,
        },
        WINE_WHITE: {
            "name": "Juffer Sonnenuhr Kabinett",
            "producer": "Fritz Haag",
            "type": "white",
            "colour": None,
            "country": "Germany",
            "region": "Mosel",
            "appellation": None,
            "classification": None,
            "grapes": ["Riesling"],
            "image": None,
            "profile": None,
        },
    },
    "bottles": [
        {
            "id": B_RED,
            "wine": WINE_RED,
            "pending": None,
            "cellar": CELLAR_HOME,
            "vintage": "2015",
            "maturity": "peak",
            "location": {
                "rack": RACK,
                "rackName": "Left cabinet",
                "position": 14,
                "shelf": 2,
                "row": 1,
                "col": 3,
            },
            "place": None,
            "personal": [
                {
                    "key": "Closure",
                    "type": "enum",
                    "unit": None,
                    "value": "cork",
                    "level": "bottle",
                    "by": "johan",
                }
            ],
        },
        {
            "id": B_WHITE,
            "wine": WINE_WHITE,
            "pending": None,
            "cellar": CELLAR_CABIN,
            "vintage": "2021",
            "maturity": "early",
            "location": None,
            "place": "Box 3",
            "personal": [],
        },
        {
            "id": B_PENDING,
            "wine": None,
            "pending": {"name": "Mystery Barolo", "producer": "Unknown"},
            "cellar": CELLAR_HOME,
            "vintage": "2016",
            "maturity": None,
            "location": None,
            "place": None,
            "personal": [],
        },
    ],
}


def _mock_inventory(aioclient_mock, status: int = 200, url: str = BASE_URL) -> None:
    if status == 200:
        aioclient_mock.get(f"{url}/api/inventory", json=INVENTORY, headers={"ETag": 'W/"tag-1"'})
    else:
        aioclient_mock.get(f"{url}/api/inventory", status=status, json={"error": "no"})


def _inventory_calls(aioclient_mock) -> list:
    return [call for call in aioclient_mock.mock_calls if str(call[1]).endswith("/api/inventory")]


async def _setup(hass: HomeAssistant, aioclient_mock, token_entry, status: int = 200) -> None:
    token_entry.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock)
    _mock_inventory(aioclient_mock, status)
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()


async def _get_bottles(hass: HomeAssistant, **data: Any) -> dict[str, Any]:
    response = await hass.services.async_call(
        DOMAIN, "get_bottles", data, blocking=True, return_response=True
    )
    assert isinstance(response, dict)
    return response


async def test_get_bottles_returns_the_inventory(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """Without filters the action answers the whole inventory, with entry and instance."""
    await _setup(hass, aioclient_mock, token_entry)

    response = await _get_bottles(hass)

    assert response["entry_id"] == token_entry.entry_id
    assert response["instance_url"] == BASE_URL
    assert response["schema"] == 1
    assert [b["id"] for b in response["bottles"]] == [B_RED, B_WHITE, B_PENDING]
    assert set(response["wines"]) == {WINE_RED, WINE_WHITE}
    assert response["cellars"] == INVENTORY["cellars"]
    assert response["racks"] == INVENTORY["racks"]
    # Nothing is fetched before someone asks: setup alone made no inventory call
    assert len(_inventory_calls(aioclient_mock)) == 1


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"search": "cote rotie"}, [B_RED]),  # accents folded, words in any order
        ({"search": "rotie GUIGAL 2015"}, [B_RED]),
        ({"search": "guigal 2016"}, []),  # every term must match
        ({"search": "cork"}, [B_RED]),  # personal field values
        ({"search": "box 3"}, [B_WHITE]),  # free-text place
        ({"search": "left cabinet"}, [B_RED]),  # rack name
        ({"search": "mystery"}, [B_PENDING]),  # a bottle waiting on a wine request
        ({"maturity": ["peak", "early"]}, [B_RED, B_WHITE]),
        ({"maturity": "early"}, [B_WHITE]),
        ({"type": ["white"]}, [B_WHITE]),
        ({"cellar": "cabin"}, [B_WHITE]),
        ({"cellar": CELLAR_HOME}, [B_RED, B_PENDING]),
        ({"cellar": "nowhere"}, []),
        ({"search": "riesling", "cellar": "Home"}, []),
    ],
)
async def test_get_bottles_filters(
    hass: HomeAssistant, aioclient_mock, token_entry, data: dict, expected: list
) -> None:
    """Search, maturity, type and cellar narrow the bottles and the wines with them."""
    await _setup(hass, aioclient_mock, token_entry)

    response = await _get_bottles(hass, **data)

    assert [b["id"] for b in response["bottles"]] == expected
    assert set(response["wines"]) == {b["wine"] for b in response["bottles"] if b["wine"]}


async def test_get_bottles_rejects_unknown_maturity(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """Maturity states are a closed set."""
    await _setup(hass, aioclient_mock, token_entry)

    with pytest.raises(vol.Invalid):
        await _get_bottles(hass, maturity=["ancient"])


async def test_repeat_calls_are_answered_from_memory(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """Within the refetch window no request goes out; after it, a conditional one."""
    await _setup(hass, aioclient_mock, token_entry)
    clock = 1000.0
    with patch.object(inventory_module, "_monotonic", side_effect=lambda: clock):
        await _get_bottles(hass)
        await _get_bottles(hass, search="guigal")
        assert len(_inventory_calls(aioclient_mock)) == 1

        clock += inventory_module.MIN_REFETCH_SECONDS + 1
        await _get_bottles(hass)

    calls = _inventory_calls(aioclient_mock)
    assert len(calls) == 2
    assert calls[1][3]["If-None-Match"] == 'W/"tag-1"'


async def test_coordinator_update_marks_the_inventory_stale(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """A poll or push refresh means bottles may have changed: ask again at once."""
    await _setup(hass, aioclient_mock, token_entry)
    await _get_bottles(hass)

    await token_entry.runtime_data.async_refresh()
    await _get_bottles(hass)

    assert len(_inventory_calls(aioclient_mock)) == 2


async def test_reload_starts_a_fresh_cache(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """A reloaded entry has a new client; the old cache is dropped with it."""
    await _setup(hass, aioclient_mock, token_entry)
    await _get_bottles(hass)

    assert await hass.config_entries.async_reload(token_entry.entry_id)
    await hass.async_block_till_done()
    assert token_entry.entry_id not in hass.data.get(inventory_module.INVENTORIES, {})
    await _get_bottles(hass)

    calls = _inventory_calls(aioclient_mock)
    assert len(calls) == 2
    assert "If-None-Match" not in (calls[1][3] or {})


@pytest.mark.parametrize("status", [403, 404])
async def test_get_bottles_on_an_older_server(
    hass: HomeAssistant, aioclient_mock, token_entry, status: int
) -> None:
    """A server without the endpoint gets an actionable error, no reauth."""
    await _setup(hass, aioclient_mock, token_entry, status=status)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _get_bottles(hass)

    assert excinfo.value.translation_key == "inventory_not_supported"
    assert excinfo.value.translation_placeholders == {"version": INVENTORY_MIN_VERSION}
    assert not hass.config_entries.flow.async_progress()


async def test_get_bottles_auth_error_starts_reauth(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """A revoked token opens the reauth flow."""
    await _setup(hass, aioclient_mock, token_entry, status=401)

    with pytest.raises(HomeAssistantError) as excinfo:
        await _get_bottles(hass)

    assert excinfo.value.translation_key == "inventory_auth_failed"
    await hass.async_block_till_done()
    assert any(
        flow["handler"] == DOMAIN and flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_get_bottles_server_error(hass: HomeAssistant, aioclient_mock, token_entry) -> None:
    """Any other API failure is a HomeAssistantError."""
    await _setup(hass, aioclient_mock, token_entry, status=500)

    with pytest.raises(HomeAssistantError) as excinfo:
        await _get_bottles(hass)

    assert excinfo.value.translation_key == "inventory_failed"


async def test_get_bottles_routes_by_entry_id(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """With two accounts the entry_id picks one; without it the call is refused."""
    await _setup(hass, aioclient_mock, token_entry)
    other = MockConfigEntry(
        domain=DOMAIN,
        title="Cellarion (other.local)",
        unique_id="http://other.local_ACCOUNT-B",
        data={"url": "http://other.local", "token": NEW_TOKEN},
        options={"scan_interval": 1800},
    )
    other.add_to_hass(hass)
    mock_cellarion_api(aioclient_mock, url="http://other.local")
    _mock_inventory(aioclient_mock, url="http://other.local")
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await _get_bottles(hass)
    response = await _get_bottles(hass, entry_id=other.entry_id)

    assert response["entry_id"] == other.entry_id
    assert response["instance_url"] == "http://other.local"


async def test_cache_keeps_its_copy_on_304() -> None:
    """A 304 keeps the held inventory and its tag; invalidate forces a new request."""
    client = AsyncMock()
    client.get_inventory.side_effect = [
        InventoryResult(data=INVENTORY, etag="t1", not_modified=False),
        InventoryResult(data=None, etag="t1", not_modified=True),
    ]
    cache = CellarionInventory(client)

    assert await cache.async_get() is INVENTORY
    cache.invalidate()
    assert await cache.async_get() is INVENTORY

    assert [c.args for c in client.get_inventory.call_args_list] == [(None,), ("t1",)]


async def test_cache_refuses_an_empty_answer() -> None:
    """A 200 without a body is an API error, never an empty cellar."""
    client = AsyncMock()
    client.get_inventory.return_value = InventoryResult(data=None, etag=None, not_modified=False)

    with pytest.raises(CellarionApiError):
        await CellarionInventory(client).async_get()


def test_filter_skips_malformed_rows() -> None:
    """Rows that are not objects are dropped instead of breaking the action."""
    data = {**INVENTORY, "bottles": [*INVENTORY["bottles"], "junk", None], "cellars": ["x"]}

    result = filter_inventory(data, search="guigal")

    assert [b["id"] for b in result["bottles"]] == [B_RED]
    assert result["cellars"] == []


# @feature frontend
async def test_image_proxy_serves_and_caches_thumbnails(
    hass: HomeAssistant, aioclient_mock, token_entry, hass_client
) -> None:
    """A signed-in user gets the thumbnail; a second request is served from memory."""
    await _setup(hass, aioclient_mock, token_entry)
    upstream = f"{BASE_URL}/api/uploads/thumbs/processed/{THUMB}"
    aioclient_mock.get(upstream, content=b"WEBP", headers={"Content-Type": "image/webp"})
    client = await hass_client()
    url = f"/api/cellarion/image/{token_entry.entry_id}/processed/{THUMB}"

    for _ in range(2):
        resp = await client.get(url)
        assert resp.status == 200
        assert await resp.read() == b"WEBP"
        assert resp.headers["Content-Type"] == "image/webp"
        assert resp.headers["Cache-Control"] == "private, max-age=86400"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    upstream_calls = [c for c in aioclient_mock.mock_calls if str(c[1]) == upstream]
    assert len(upstream_calls) == 1
    # The token never goes to the public uploads mount
    assert "Authorization" not in (upstream_calls[0][3] or {})


async def test_image_proxy_needs_a_signed_in_user(
    hass: HomeAssistant, aioclient_mock, token_entry, hass_client_no_auth
) -> None:
    """Anonymous requests are refused before anything is fetched."""
    await _setup(hass, aioclient_mock, token_entry)
    client = await hass_client_no_auth()

    resp = await client.get(f"/api/cellarion/image/{token_entry.entry_id}/processed/{THUMB}")

    assert resp.status == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/cellarion/image/{entry}/receipts/" + THUMB,
        "/api/cellarion/image/{entry}/processed/evil.svg",
        "/api/cellarion/image/{entry}/processed/x.webp",
        "/api/cellarion/image/{entry}/processed/x%2F..%2Fy.webp.webp",
        "/api/cellarion/image/not-an-entry/processed/" + THUMB,
    ],
)
async def test_image_proxy_refuses_other_paths(
    hass: HomeAssistant, aioclient_mock, token_entry, hass_client, path: str
) -> None:
    """Only thumbnails of a loaded Cellarion entry are proxied; nothing else is fetched."""
    await _setup(hass, aioclient_mock, token_entry)
    client = await hass_client()
    before = len(aioclient_mock.mock_calls)

    resp = await client.get(path.format(entry=token_entry.entry_id))

    # 400: Home Assistant's own middleware refuses the encoded traversal first
    assert resp.status in (400, 404)
    assert len(aioclient_mock.mock_calls) == before


@pytest.mark.parametrize(
    ("status", "content_type"),
    [(500, "image/webp"), (200, "image/svg+xml"), (200, "text/html")],
)
async def test_image_proxy_upstream_failures(
    hass: HomeAssistant,
    aioclient_mock,
    token_entry,
    hass_client,
    status: int,
    content_type: str,
) -> None:
    """Upstream errors and non-raster types become a 502, never passed through."""
    await _setup(hass, aioclient_mock, token_entry)
    aioclient_mock.get(
        f"{BASE_URL}/api/uploads/thumbs/processed/{THUMB}",
        status=status,
        content=b"<svg onload=alert(1)>",
        headers={"Content-Type": content_type},
    )
    client = await hass_client()

    resp = await client.get(f"/api/cellarion/image/{token_entry.entry_id}/processed/{THUMB}")

    assert resp.status == 502


# @endfeature
