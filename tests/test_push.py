"""Tests for the push (SSE) listener."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.cellarion.api import (
    CellarionPushForbidden,
    CellarionPushNotSupported,
)
from custom_components.cellarion.const import DOMAIN
from custom_components.cellarion.push import (
    PUSH_FORBIDDEN_ISSUE,
    PUSH_POLL_INTERVAL,
    async_push_listener,
)


class FakeClient:
    """Stub client whose events_stream yields scripted events."""

    def __init__(self, events=(), exc=None):
        self._events = events
        self._exc = exc

    async def events_stream(self):
        if self._exc:
            raise self._exc
        yield "_connected"
        for event in self._events:
            yield event


class FakeCoordinator:
    def __init__(self, hass: HomeAssistant, client) -> None:
        self.hass = hass
        self.client = client
        self.update_interval = timedelta(minutes=30)
        self.async_request_refresh = AsyncMock()
        self.interval_history: list[timedelta] = []

    def __setattr__(self, name, value):
        if name == "update_interval":
            self.__dict__.setdefault("interval_history", []).append(value)
        super().__setattr__(name, value)


async def _run_listener(coordinator, runtime: float = 0.3) -> None:
    task = asyncio.create_task(async_push_listener(coordinator))
    await asyncio.sleep(runtime)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_events_trigger_refresh_and_interval(hass: HomeAssistant) -> None:
    """Connected stream relaxes polling; events trigger refreshes."""
    coordinator = FakeCoordinator(
        hass, FakeClient(events=("ready", "stats_changed"))
    )
    await _run_listener(coordinator)

    # ready + stats_changed + post-disconnect catch-up
    assert coordinator.async_request_refresh.await_count == 3
    assert PUSH_POLL_INTERVAL in coordinator.interval_history
    # Interval restored after the stream dropped
    assert coordinator.update_interval == timedelta(minutes=30)


async def test_not_supported_falls_back(hass: HomeAssistant) -> None:
    """A server without push leaves polling untouched."""
    coordinator = FakeCoordinator(
        hass, FakeClient(exc=CellarionPushNotSupported("nope"))
    )
    await _run_listener(coordinator)

    assert coordinator.async_request_refresh.await_count == 0
    assert coordinator.update_interval == timedelta(minutes=30)


async def test_forbidden_creates_repair_issue(hass: HomeAssistant) -> None:
    """A 403 stream creates a repair issue and stops the listener."""
    coordinator = FakeCoordinator(
        hass, FakeClient(exc=CellarionPushForbidden("no read scope"))
    )
    task = asyncio.create_task(async_push_listener(coordinator))
    await asyncio.sleep(0.1)
    # Listener returned on its own — no cancel needed
    assert task.done()

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, PUSH_FORBIDDEN_ISSUE) is not None
