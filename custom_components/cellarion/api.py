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


class CellarionScopeError(CellarionApiError):
    """The API token is valid but lacks a required scope."""


class CellarionTokensNotSupported(CellarionApiError):
    """The server does not support personal API tokens."""


class CellarionApiClient:
    """Async API client for Cellarion."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        email: str | None = None,
        password: str | None = None,
        token: str | None = None,
    ) -> None:
        self._session = session
        self._url = url.rstrip("/")
        self._email = email
        self._password = password
        # Personal API token (cel_...) — static, never refreshed via login
        self._api_token = token
        self._token: str | None = token

    async def authenticate(self) -> bool:
        """Authenticate and store JWT token."""
        if self._api_token:
            # Static API token: there is nothing to refresh. Reaching this
            # means the server rejected it — revoked or invalid.
            raise CellarionAuthError("API token rejected (revoked or invalid)")

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

    async def _send(
        self, method: str, path: str, json: dict | None
    ) -> aiohttp.ClientResponse:
        """Issue one request, mapping transport errors to CellarionApiError."""
        try:
            return await self._session.request(
                method,
                f"{self._url}{path}",
                headers={"Authorization": f"Bearer {self._token}"},
                json=json,
                timeout=aiohttp.ClientTimeout(total=30),
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CellarionApiError(f"Request failed: {err}") from err

    async def _request(
        self, method: str, path: str, json: dict | None = None
    ) -> dict:
        """Make an authenticated API request with auto-retry on 401."""
        if not self._token:
            await self.authenticate()

        resp = await self._send(method, path, json)
        # Always release the connection back to the pool, even on error paths.
        try:
            if resp.status == 401:
                # Token expired — re-authenticate and retry once
                _LOGGER.debug("Token expired, re-authenticating")
                resp.close()
                await self.authenticate()
                resp = await self._send(method, path, json)
                if resp.status == 401:
                    raise CellarionAuthError(
                        "Authentication rejected after retry"
                    )

            if resp.status == 403 and self._api_token:
                # Valid token, missing scope — a config error; re-login won't help
                raise CellarionScopeError(
                    f"API token lacks the scope for {method} {path}"
                )

            # Accept the 2xx success range: action endpoints (e.g. consume) may
            # answer 201/204 rather than 200.
            if not 200 <= resp.status < 300:
                detail = ""
                try:
                    detail = (await resp.json()).get("error", "")
                except Exception:  # noqa: BLE001 — best-effort error body
                    pass
                raise CellarionApiError(
                    f"{method} {path} returned status {resp.status}"
                    + (f": {detail}" if detail else "")
                )

            # getattr keeps this working against test doubles that don't
            # implement content_length; real aiohttp always provides it.
            if resp.status == 204 or getattr(resp, "content_length", None) == 0:
                return {}
            try:
                return await resp.json()
            except (aiohttp.ClientError, ValueError):
                # No/!JSON body on a success status — nothing to return
                return {}
        finally:
            resp.close()

    async def get_stats_overview(self) -> dict:
        """Fetch collection statistics."""
        return await self._request("GET", "/api/stats/overview")

    async def get_cellars(self) -> dict:
        """Fetch user's cellars."""
        return await self._request("GET", "/api/cellars")

    async def get_account_id(self) -> str | None:
        """Best-effort stable account id, used to verify reauth stays on the
        same account.

        Reads the scoped identity endpoint /api/auth/whoami, which returns
        just {"id": ...}. Returns None when the server or credential can't
        provide one — an older server without the endpoint (404), or a token
        whose scope doesn't include it (403). Callers treat None as "unknown"
        and skip the check, never as an error. The nested "user" fallback keeps
        it working against the fuller /api/auth/me shape too.
        """
        try:
            data = await self._request("GET", "/api/auth/whoami")
        except CellarionApiError:
            return None
        if not isinstance(data, dict):
            # A proxy/edge server could answer 200 with a JSON array or scalar
            return None
        user = data.get("user")
        user = user if isinstance(user, dict) else {}
        account_id = data.get("id") or user.get("id") or user.get("_id")
        return str(account_id) if account_id else None

    async def get_notifications(self) -> dict:
        """Fetch notifications with unread count."""
        return await self._request("GET", "/api/notifications")

    async def async_create_api_token(self, name: str, scopes: list[str]) -> str:
        """Mint a personal API token. Requires password-based login.

        The Cellarion endpoint requires the account password in the body as
        confirmation; the caller stores only the returned token.
        """
        if not self._password:
            raise CellarionApiError("Password login required to mint a token")
        if not self._token:
            await self.authenticate()

        try:
            resp = await self._session.post(
                f"{self._url}/api/tokens",
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "name": name,
                    "scopes": scopes,
                    "password": self._password,
                },
                timeout=aiohttp.ClientTimeout(total=15),
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CellarionApiError(f"Token creation failed: {err}") from err

        if resp.status in (404, 405, 501):
            raise CellarionTokensNotSupported(
                "Server does not support API tokens"
            )
        if resp.status in (401, 403):
            raise CellarionAuthError("Password confirmation rejected")
        if resp.status == 429:
            raise CellarionApiError("Rate limited by Cellarion, try again later")
        if resp.status not in (200, 201):
            raise CellarionApiError(
                f"Token creation returned status {resp.status}"
            )

        data = await resp.json()
        token = data.get("token")
        if not token:
            raise CellarionApiError("No token in creation response")
        return token

    async def get_peak_bottles(self, limit: int = 10) -> dict:
        """Fetch bottles currently in their peak drink window."""
        return await self._request(
            "GET", f"/api/bottles?maturity=peak&limit={limit}"
        )

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
