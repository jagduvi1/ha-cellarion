"""Push event listener for Cellarion.

Holds a long-lived SSE connection to the Cellarion server (outbound from
HA, so no port forwarding or cloud relay is needed). Every server event
triggers a debounced coordinator refresh; the data itself still comes from
the REST endpoints.

While the stream is connected, polling is relaxed to a slow safety-net
interval — push carries the updates and the server sees far fewer logins.
If the server does not support push (older Cellarion), the integration
quietly stays on regular polling.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import timedelta

from .api import (
    CellarionApiError,
    CellarionAuthError,
    CellarionPushNotSupported,
)
from .coordinator import CellarionCoordinator

_LOGGER = logging.getLogger(__name__)

RECONNECT_MIN_SECONDS = 30
RECONNECT_MAX_SECONDS = 1800
UNSUPPORTED_RETRY_SECONDS = 6 * 3600
# Safety-net poll interval while push is delivering updates
PUSH_POLL_INTERVAL = timedelta(hours=6)


async def async_push_listener(coordinator: CellarionCoordinator) -> None:
    """Run forever: connect, forward events, reconnect with backoff."""
    base_interval = coordinator.update_interval
    unsupported_logged = False
    backoff = RECONNECT_MIN_SECONDS

    try:
        while True:
            connected = False
            try:
                async for event in coordinator.client.events_stream():
                    if event == "_connected":
                        connected = True
                        backoff = RECONNECT_MIN_SECONDS
                        coordinator.update_interval = PUSH_POLL_INTERVAL
                        _LOGGER.debug(
                            "Push stream connected; polling relaxed to %s",
                            PUSH_POLL_INTERVAL,
                        )
                        continue
                    _LOGGER.debug("Push event: %s", event)
                    await coordinator.async_request_refresh()
            except CellarionPushNotSupported:
                if not unsupported_logged:
                    _LOGGER.info(
                        "Cellarion server does not offer the push event "
                        "stream; staying on polling"
                    )
                    unsupported_logged = True
                await asyncio.sleep(UNSUPPORTED_RETRY_SECONDS)
                continue
            except CellarionAuthError:
                # Bad credentials — the polling path raises
                # ConfigEntryAuthFailed and starts the reauth flow; a
                # successful reauth reloads the entry and restarts us.
                _LOGGER.debug("Push stream auth failed; stopping listener")
                return
            except CellarionApiError as err:
                _LOGGER.debug("Push stream error: %s", err)
            finally:
                if connected:
                    coordinator.update_interval = base_interval

            if connected:
                # Stream dropped: resume normal polling promptly and pick
                # up anything missed during the gap.
                await coordinator.async_request_refresh()

            await asyncio.sleep(backoff * (1 + random.random() * 0.25))
            backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)
    finally:
        coordinator.update_interval = base_interval
