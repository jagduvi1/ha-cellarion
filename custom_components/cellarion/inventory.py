"""The account's inventory for the inventory card and the get_bottles action.

HACS-only for now (feature `inventory`; tools/export_core.py leaves it out of
every core stage). Cellarion builds the whole inventory in one request
(GET /api/inventory, Cellarion 1.275+) and answers a repeat with 304 when
nothing changed, so the cache below costs the server one small query per
card open. Nothing is fetched in the background: no card open, no request.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
import unicodedata

from homeassistant.core import HomeAssistant, callback
from homeassistant.util.hass_dict import HassKey
from pycellarion import CellarionApiError, CellarionClient

from .const import DOMAIN
from .coordinator import CellarionConfigEntry

# The Cellarion version that serves GET /api/inventory; named in the error
# the action raises against older servers.
INVENTORY_MIN_VERSION = "1.275"
# A card open within this many seconds of the last fetch is answered from
# memory; anything later asks the server, which mostly answers 304.
MIN_REFETCH_SECONDS = 30
MATURITY_STATES = ["not-ready", "early", "peak", "late", "declining"]


def _monotonic() -> float:
    """Seconds on a clock that never jumps (patched in tests)."""
    return time.monotonic()


class CellarionInventory:
    """Last inventory of one account, refreshed conditionally on demand."""

    def __init__(self, client: CellarionClient) -> None:
        """Start empty; the first async_get fetches."""
        self.client = client
        self._data: dict[str, Any] | None = None
        self._etag: str | None = None
        self._fetched_at = 0.0
        self._stale = False
        self._lock = asyncio.Lock()

    @callback
    def invalidate(self) -> None:
        """Ask the server again on the next read (something changed)."""
        self._stale = True

    async def async_get(self) -> dict[str, Any]:
        """Return the inventory, asking the server only when it may have changed."""
        async with self._lock:
            now = _monotonic()
            if (
                self._data is not None
                and not self._stale
                and now - self._fetched_at < MIN_REFETCH_SECONDS
            ):
                return self._data
            result = await self.client.get_inventory(self._etag if self._data else None)
            if not result.not_modified:
                if result.data is None:
                    raise CellarionApiError("Cellarion returned an empty inventory")
                self._data = result.data
                self._etag = result.etag
            elif self._data is None:  # pragma: no cover - a 304 needs a tag we sent
                raise CellarionApiError("Cellarion answered 304 to a first request")
            self._fetched_at = now
            self._stale = False
            return self._data


INVENTORIES: HassKey[dict[str, CellarionInventory]] = HassKey(f"{DOMAIN}_inventories")


@callback
def async_get_inventory(hass: HomeAssistant, entry: CellarionConfigEntry) -> CellarionInventory:
    """Return the entry's inventory cache, creating it on first use."""
    caches = hass.data.setdefault(INVENTORIES, {})
    coordinator = entry.runtime_data
    inventory = caches.get(entry.entry_id)
    if inventory is not None and inventory.client is coordinator.client:
        return inventory

    inventory = CellarionInventory(coordinator.client)
    caches[entry.entry_id] = inventory
    # Every coordinator update (a push event, a poll, a consume) may mean
    # changed bottles: the next card open asks the server instead of memory.
    entry.async_on_unload(coordinator.async_add_listener(inventory.invalidate))

    @callback
    def _forget() -> None:
        if caches.get(entry.entry_id) is inventory:
            del caches[entry.entry_id]

    entry.async_on_unload(_forget)
    return inventory


def _fold(text: str) -> str:
    """Lower-case text without accents, for matching "Cote Rotie" to "Côte-Rôtie"."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _searchable(bottle: dict[str, Any], wine: dict[str, Any]) -> str:
    """Everything a search term may match for one bottle, folded."""
    pending = _as_dict(bottle.get("pending"))
    location = _as_dict(bottle.get("location"))
    parts: list[Any] = [
        wine.get("name"),
        wine.get("producer"),
        wine.get("region"),
        wine.get("country"),
        wine.get("appellation"),
        wine.get("classification"),
        *(wine.get("grapes") or []),
        bottle.get("vintage"),
        bottle.get("size"),
        bottle.get("place"),
        location.get("rackName"),
        pending.get("name"),
        pending.get("producer"),
    ]
    parts.extend(_as_dict(entry).get("value") for entry in bottle.get("personal") or [])
    return _fold(" ".join(str(part) for part in parts if part not in (None, "")))


def filter_inventory(
    data: dict[str, Any],
    *,
    search: str | None = None,
    maturity: list[str] | None = None,
    types: list[str] | None = None,
    cellar: str | None = None,
) -> dict[str, Any]:
    """The inventory narrowed to the matching bottles and the wines they use.

    Search terms are separated by spaces and must all match. `cellar` is a
    cellar id or name.
    """
    wines: dict[str, Any] = _as_dict(data.get("wines"))
    cellars = [c for c in data.get("cellars") or [] if isinstance(c, dict)]
    terms = _fold(search).split() if search else []
    cellar_ids: set[str] | None = None
    if cellar:
        wanted = _fold(cellar)
        cellar_ids = {
            str(c.get("id"))
            for c in cellars
            if str(c.get("id")) == cellar or _fold(str(c.get("name") or "")) == wanted
        }

    bottles = []
    for bottle in data.get("bottles") or []:
        if not isinstance(bottle, dict):
            continue
        wine = _as_dict(wines.get(str(bottle.get("wine"))))
        if cellar_ids is not None and str(bottle.get("cellar")) not in cellar_ids:
            continue
        if maturity and bottle.get("maturity") not in maturity:
            continue
        if types and wine.get("type") not in types:
            continue
        if terms:
            text = _searchable(bottle, wine)
            if not all(term in text for term in terms):
                continue
        bottles.append(bottle)

    used = {str(b.get("wine")) for b in bottles if b.get("wine")}
    return {
        "schema": data.get("schema"),
        "generatedAt": data.get("generatedAt"),
        "cellars": cellars,
        "racks": [r for r in data.get("racks") or [] if isinstance(r, dict)],
        "wines": {wine_id: wine for wine_id, wine in wines.items() if wine_id in used},
        "bottles": bottles,
    }
