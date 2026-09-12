"""Tests for the diagnostics dump — the one place a regression leaks a secret."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant

from custom_components.cellarion.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import BASE_URL, TEST_TOKEN, mock_cellarion_api


async def test_diagnostics_redact_secrets_and_identities(
    hass: HomeAssistant, aioclient_mock, token_entry
) -> None:
    """Token, URL, account id and record ids never appear in the dump."""
    token_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        token_entry, data={**token_entry.data, "account_id": "ACCOUNT-A"}
    )
    mock_cellarion_api(aioclient_mock)
    assert await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()
    # A realistic cellar object: the owner id and members ride along
    token_entry.runtime_data.data["cellars"] = [
        {"_id": "CELLAR-1", "name": "Main", "user": "ACCOUNT-A", "members": ["X"]}
    ]

    diag = await async_get_config_entry_diagnostics(hass, token_entry)
    dump = json.dumps(diag)

    assert TEST_TOKEN not in dump
    assert BASE_URL not in dump
    assert "ACCOUNT-A" not in dump
    assert "CELLAR-1" not in dump
    assert "PEAK-SOON" not in dump  # bottle ids are records too
    assert "auth_method" not in diag["entry"]
    # Harmless statistics stay readable
    assert diag["coordinator"]["data"]["overview"]["totalBottles"] == 42
    assert diag["coordinator"]["data"]["cellars"][0]["name"] == "Main"
