"""API client for Cellarion."""

from __future__ import annotations

import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)


class CellarionApiError(Exception):
    """Base exception for Cellarion API errors."""


class CellarionAuthError(CellarionApiError):
    """Authentication failed."""


class CellarionPushNotSupported(CellarionApiError):
    """The server does not offer the push event stream."""


class CellarionPushForbidden(CellarionApiError):
    """The credential lacks the scope required for the push stream."""


class CellarionApiClient:
    """Async API client for Cellarion."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        email: str,
        password: str,
    ) -> None:
        self._session = session
        self._url = url.rstrip("/")
        self._email = email
        self._password = password
        self._token: str | None = None

    async def authenticate(self) -> bool:
        """Authenticate and store JWT token."""
        login_url = f"{self._url}/api/auth/login"
        _LOGGER.debug("Authenticating to %s", login_url)
        try:
            resp = await self._session.post(
                login_url,
                # The Cellarion login endpoint takes "username", which matches
                # against both username and email.
                json={"username": self._email, "password": self._password},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.error("Connection to %s failed: %s", login_url, err)
            raise CellarionApiError(f"Connection failed: {err}") from err

        if resp.status in (400, 401):
            raise CellarionAuthError("Invalid email or password")
        if resp.status == 403:
            raise CellarionAuthError("Account not verified or access denied")
        if resp.status == 429:
            raise CellarionApiError("Rate limited by Cellarion, try again later")
        if resp.status != 200:
            raise CellarionApiError(f"Login failed with status {resp.status}")

        data = await resp.json()
        self._token = data.get("token")
        if not self._token:
            raise CellarionApiError("No token in login response")
        return True

    async def _request(
        self, method: str, path: str, json: dict | None = None
    ) -> dict:
        """Make an authenticated API request with auto-retry on 401."""
        if not self._token:
            await self.authenticate()

        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            resp = await self._session.request(
                method,
                f"{self._url}{path}",
                headers=headers,
                json=json,
                timeout=aiohttp.ClientTimeout(total=30),
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CellarionApiError(f"Request failed: {err}") from err

        if resp.status == 401:
            # Token expired — re-authenticate and retry once
            _LOGGER.debug("Token expired, re-authenticating")
            await self.authenticate()
            headers = {"Authorization": f"Bearer {self._token}"}
            try:
                resp = await self._session.request(
                    method,
                    f"{self._url}{path}",
                    headers=headers,
                    json=json,
                    timeout=aiohttp.ClientTimeout(total=30),
                )
            except (aiohttp.ClientError, TimeoutError) as err:
                raise CellarionApiError(f"Retry failed: {err}") from err
            if resp.status == 401:
                raise CellarionAuthError("Authentication rejected after retry")

        if resp.status != 200:
            detail = ""
            try:
                detail = (await resp.json()).get("error", "")
            except Exception:  # noqa: BLE001 — best-effort error body
                pass
            raise CellarionApiError(
                f"{method} {path} returned status {resp.status}"
                + (f": {detail}" if detail else "")
            )

        return await resp.json()

    async def get_stats_overview(self) -> dict:
        """Fetch collection statistics."""
        return await self._request("GET", "/api/stats/overview")

    async def get_cellars(self) -> dict:
        """Fetch user's cellars."""
        return await self._request("GET", "/api/cellars")

    async def get_notifications(self) -> dict:
        """Fetch notifications with unread count."""
        return await self._request("GET", "/api/notifications")

    async def consume_bottle(
        self,
        bottle_id: str,
        reason: str = "drank",
        rating: float | None = None,
        note: str | None = None,
    ) -> dict:
        """Mark a bottle as consumed (drank/gifted/sold/other)."""
        body: dict = {"reason": reason}
        if rating is not None:
            body["rating"] = rating
        if note:
            body["note"] = note
        return await self._request(
            "POST", f"/api/bottles/{bottle_id}/consume", json=body
        )

    async def events_stream(self):
        """Yield push event names from the server's SSE stream.

        Yields the sentinel "_connected" once the stream is open, then one
        item per server-sent event. Raises CellarionPushNotSupported when
        the server has no stream endpoint (integration falls back to
        polling only).
        """
        if not self._token:
            await self.authenticate()

        url = f"{self._url}/api/events/stream"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "text/event-stream",
        }
        # sock_read=90 → three missed 25s server heartbeats = dead stream
        timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=90)

        try:
            resp = await self._session.get(url, headers=headers, timeout=timeout)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CellarionApiError(f"Push stream connection failed: {err}") from err

        if resp.status == 401:
            resp.close()
            await self.authenticate()
            headers["Authorization"] = f"Bearer {self._token}"
            try:
                resp = await self._session.get(
                    url, headers=headers, timeout=timeout
                )
            except (aiohttp.ClientError, TimeoutError) as err:
                raise CellarionApiError(f"Push stream retry failed: {err}") from err
            if resp.status == 401:
                resp.close()
                raise CellarionAuthError("Push stream rejected credentials")

        if resp.status == 403:
            # API token without the 'read' scope — a configuration error the
            # user must fix; retrying or falling back silently would hide it
            resp.close()
            raise CellarionPushForbidden(
                "Credential lacks the 'read' scope for the event stream"
            )
        if resp.status in (404, 405, 501):
            resp.close()
            raise CellarionPushNotSupported("Server has no /api/events/stream")
        if resp.status != 200:
            status = resp.status
            resp.close()
            raise CellarionApiError(f"Push stream returned status {status}")
        if not resp.content_type.startswith("text/event-stream"):
            # A proxy or SPA fallback answered instead of the stream route
            resp.close()
            raise CellarionPushNotSupported(
                f"Expected text/event-stream, got {resp.content_type}"
            )

        try:
            yield "_connected"
            event_name: str | None = None
            async for raw in resp.content:
                line = raw.decode("utf-8", "ignore").rstrip("\r\n")
                if not line:
                    if event_name:
                        yield event_name
                    event_name = None
                    continue
                if line.startswith(":"):  # heartbeat comment
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:") and event_name is None:
                    event_name = "message"
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CellarionApiError(f"Push stream read failed: {err}") from err
        finally:
            resp.close()

    async def get_health(self) -> dict:
        """Fetch service health (no auth required, but we use it anyway)."""
        try:
            resp = await self._session.get(
                f"{self._url}/api/health",
                timeout=aiohttp.ClientTimeout(total=10),
            )
            return await resp.json()
        except (aiohttp.ClientError, TimeoutError):
            return {"status": "unreachable"}
