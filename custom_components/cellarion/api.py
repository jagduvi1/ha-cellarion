"""API client for Cellarion."""

from __future__ import annotations

import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)


class CellarionApiError(Exception):
    """Base exception for Cellarion API errors."""


class CellarionAuthError(CellarionApiError):
    """Authentication failed."""


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
                json={"email": self._email, "password": self._password},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        except aiohttp.ClientError as err:
            _LOGGER.error("Connection to %s failed: %s", login_url, err)
            raise CellarionApiError(f"Connection failed: {err}") from err

        if resp.status == 401:
            raise CellarionAuthError("Invalid email or password")
        if resp.status != 200:
            raise CellarionApiError(f"Login failed with status {resp.status}")

        data = await resp.json()
        self._token = data.get("token")
        if not self._token:
            raise CellarionApiError("No token in login response")
        return True

    async def _request(self, method: str, path: str) -> dict:
        """Make an authenticated API request with auto-retry on 401."""
        if not self._token:
            await self.authenticate()

        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            resp = await self._session.request(
                method,
                f"{self._url}{path}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            )
        except aiohttp.ClientError as err:
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
                    timeout=aiohttp.ClientTimeout(total=30),
                )
            except aiohttp.ClientError as err:
                raise CellarionApiError(f"Retry failed: {err}") from err

        if resp.status != 200:
            raise CellarionApiError(
                f"{method} {path} returned status {resp.status}"
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

    async def get_health(self) -> dict:
        """Fetch service health (no auth required, but we use it anyway)."""
        try:
            resp = await self._session.get(
                f"{self._url}/api/health",
                timeout=aiohttp.ClientTimeout(total=10),
            )
            return await resp.json()
        except aiohttp.ClientError:
            return {"status": "unreachable"}
