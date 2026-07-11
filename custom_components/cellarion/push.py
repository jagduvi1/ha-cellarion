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
import time
from datetime import timedelta

from homeassistant.helpers import issue_registry as ir

from .api import (
    CellarionApiError,
    CellarionAuthError,
    CellarionPushForbidden,
    CellarionPushNotSupported,
)
from .const import DOMAIN
from .coordinator import CellarionCoordinator

_LOGGER = logging.getLogger(__name__)

PUSH_FORBIDDEN_ISSUE = "push_forbidden"

RECONNECT_MIN_SECONDS = 30
RECONNECT_MAX_SECONDS = 1800
UNSUPPORTED_RETRY_SECONDS = 6 * 3600
# A stream that stays connected at least this long counts as healthy — only
# then do we reset the reconnect backoff. Guards against a proxy/overloaded
# upstream that accepts the request (200) and drops it immediately: without
# this, every such cycle would reset backoff and pin us to a ~30s reconnect
# storm instead of backing off.
STABLE_CONNECTION_SECONDS = RECONNECT_MIN_SECONDS
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
            connected_at: float | None = None
            try:
                async for event in coordinator.client.events_stream():
                    if event == "_connected":
                        connected = True
                        connected_at = time.monotonic()
                        # Note: backoff is NOT reset here — the 200 only proves
                        # the request was accepted, not that the stream is
                        # stable. It is reset below once the connection has
                        # lasted STABLE_CONNECTION_SECONDS.
                        coordinator.update_interval = PUSH_POLL_INTERVAL
                        ir.async_delete_issue(
                            coordinator.hass, DOMAIN, PUSH_FORBIDDEN_ISSUE
                        )
                        _LOGGER.debug(
                            "Push stream connected; polling relaxed to %s",
                            PUSH_POLL_INTERVAL,
                        )
                        continue
                    _LOGGER.debug("Push event: %s", event)
                    await coordinator.async_request_refresh()
            except CellarionPushForbidden:
                # Scope misconfiguration — surface it, keep polling, and
                # don't hammer an endpoint that will keep saying no.
                _LOGGER.error(
                    "Cellarion denied the push stream: the API token lacks "
                    "the 'read' scope. Create a new token with the read "
                    "scope (Cellarion → Settings → API tokens) and "
                    "reconfigure the integration. Polling continues to work"
                )
                ir.async_create_issue(
                    coordinator.hass,
                    DOMAIN,
                    PUSH_FORBIDDEN_ISSUE,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=PUSH_FORBIDDEN_ISSUE,
                )
                return
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
                # A connection that stayed up long enough was healthy — reset
                # the backoff so the next reconnect is prompt. One that dropped
                # almost immediately (flapping proxy/upstream) leaves the
                # backoff growing so we don't hammer it.
                if (
                    connected_at is not None
                    and time.monotonic() - connected_at >= STABLE_CONNECTION_SECONDS
                ):
                    backoff = RECONNECT_MIN_SECONDS
                # Stream dropped: resume normal polling promptly and pick
                # up anything missed during the gap.
                await coordinator.async_request_refresh()

            await asyncio.sleep(backoff * (1 + random.random() * 0.25))
            backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)
    finally:
        coordinator.update_interval = base_interval
