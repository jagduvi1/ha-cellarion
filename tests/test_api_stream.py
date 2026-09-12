"""Tests for the API client's stream parser, retries and error mapping.

These use a scripted fake session instead of aioclient_mock so that
sequences of responses (401 → re-login → retry) and streamed bodies can be
expressed directly.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import aiohttp
import pytest

from custom_components.cellarion.api import (
    CellarionApiClient,
    CellarionApiError,
    CellarionAuthError,
    CellarionPushForbidden,
    CellarionPushNotSupported,
    CellarionScopeError,
    CellarionTokensNotSupported,
)

from .conftest import BASE_URL, TEST_TOKEN


class FakeResponse:
    """Just enough of aiohttp.ClientResponse for the client."""

    def __init__(
        self,
        status: int = 200,
        json: Any = None,
        *,
        content_type: str = "application/json",
        lines: Iterable[bytes] = (),
        read_error: Exception | None = None,
        content_length: int | None = None,
    ) -> None:
        self.status = status
        self._json = json
        self.content_type = content_type
        self._lines = list(lines)
        self._read_error = read_error
        self.content_length = content_length
        self.closed = False

    async def json(self) -> Any:
        if self._json is None:
            raise ValueError("not json")
        return self._json

    def close(self) -> None:
        self.closed = True

    @property
    def content(self) -> FakeResponse:
        return self

    def __aiter__(self) -> FakeResponse:
        self._iter = iter(self._lines)
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._iter)
        except StopIteration:
            if self._read_error:
                err, self._read_error = self._read_error, None
                raise err from None
            raise StopAsyncIteration from None


class FakeSession:
    """Hands out scripted responses in order and records every call."""

    def __init__(self, responses: Iterable[FakeResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _next(self, method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        return self._next(method, url, kwargs)

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._next("GET", url, kwargs)

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._next("POST", url, kwargs)


def token_client(*responses: FakeResponse | Exception) -> tuple[CellarionApiClient, FakeSession]:
    session = FakeSession(responses)
    return CellarionApiClient(session, BASE_URL, token=TEST_TOKEN), session  # type: ignore[arg-type]


def password_client(
    *responses: FakeResponse | Exception,
) -> tuple[CellarionApiClient, FakeSession]:
    session = FakeSession(responses)
    return CellarionApiClient(session, BASE_URL, "user@example.com", "pw"), session  # type: ignore[arg-type]


SSE_BODY = [
    b": heartbeat\n",
    b"\n",
    b"event: stats_changed\n",
    b"data: {}\n",
    b"\n",
    b"data: bare-data-event\n",
    b"\n",
    b"event: ignored-without-blank-line\n",
]


async def collect(client: CellarionApiClient) -> list[str]:
    return [event async for event in client.events_stream()]


# ── SSE stream ───────────────────────────────────────────────────────


async def test_stream_parses_events_and_heartbeats() -> None:
    """Named events, bare data lines and heartbeats map to the right yields."""
    client, session = token_client(
        FakeResponse(200, content_type="text/event-stream", lines=SSE_BODY)
    )
    assert await collect(client) == ["_connected", "stats_changed", "message"]
    assert session.calls[0][2]["headers"]["Accept"] == "text/event-stream"


async def test_stream_401_relogs_once_for_password_clients() -> None:
    """A rejected JWT triggers one login and a retry; a second 401 is fatal."""
    client, session = password_client(
        FakeResponse(200, {"token": "jwt-1"}),  # initial login
        FakeResponse(401),  # stream rejects
        FakeResponse(200, {"token": "jwt-2"}),  # re-login
        FakeResponse(200, content_type="text/event-stream", lines=[]),
    )
    assert await collect(client) == ["_connected"]
    assert session.calls[-1][2]["headers"]["Authorization"] == "Bearer jwt-2"

    client, _ = password_client(
        FakeResponse(200, {"token": "jwt-1"}),
        FakeResponse(401),
        FakeResponse(200, {"token": "jwt-2"}),
        FakeResponse(401),
    )
    with pytest.raises(CellarionAuthError):
        await collect(client)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (FakeResponse(403), CellarionPushForbidden),
        (FakeResponse(404), CellarionPushNotSupported),
        (FakeResponse(501), CellarionPushNotSupported),
        (FakeResponse(200, content_type="text/html"), CellarionPushNotSupported),
        (FakeResponse(500), CellarionApiError),
        (aiohttp.ClientError("down"), CellarionApiError),
    ],
)
async def test_stream_status_mapping(response: Any, expected: type[Exception]) -> None:
    """Every non-stream answer maps to the exception the listener expects."""
    client, _ = token_client(response)
    with pytest.raises(expected):
        await collect(client)


async def test_stream_read_errors_become_api_errors() -> None:
    """Transport errors and over-long lines both surface as CellarionApiError."""
    for err in (aiohttp.ClientPayloadError("cut"), ValueError("Line is too long")):
        client, _ = token_client(
            FakeResponse(
                200,
                content_type="text/event-stream",
                lines=[b"event: a\n", b"\n"],
                read_error=err,
            )
        )
        got: list[str] = []
        with pytest.raises(CellarionApiError):
            async for event in client.events_stream():
                got.append(event)
        assert got == ["_connected", "a"]


# ── Authenticated requests ───────────────────────────────────────────


async def test_request_relogs_once_on_401_for_password_clients() -> None:
    """An expired JWT is refreshed transparently, exactly once."""
    client, session = password_client(
        FakeResponse(200, {"token": "jwt-1"}),
        FakeResponse(401),
        FakeResponse(200, {"token": "jwt-2"}),
        FakeResponse(200, {"stats": {}}),
    )
    assert await client.get_stats_overview() == {"stats": {}}
    assert [c[0] for c in session.calls] == ["POST", "GET", "POST", "GET"]
    assert session.calls[-1][2]["headers"]["Authorization"] == "Bearer jwt-2"


async def test_request_error_body_detail_is_included() -> None:
    client, _ = token_client(FakeResponse(500, {"error": "boom"}))
    with pytest.raises(CellarionApiError, match="status 500: boom"):
        await client.get_cellars()


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(204),
        FakeResponse(200, content_length=0),
        FakeResponse(200, content_type="text/plain"),  # non-JSON success body
        FakeResponse(200, ["not", "a", "dict"]),
    ],
)
async def test_request_empty_or_odd_success_bodies_give_empty_dict(response: FakeResponse) -> None:
    client, _ = token_client(response)
    assert await client.consume_bottle("6a50805b785f507654afdc51") == {}
    assert response.closed


async def test_scope_error_only_for_api_tokens() -> None:
    """403 is a scope error with a token, a plain API error with a password."""
    client, _ = token_client(FakeResponse(403, {"error": "scope"}))
    with pytest.raises(CellarionScopeError):
        await client.get_cellars()

    client, _ = password_client(
        FakeResponse(200, {"token": "jwt"}), FakeResponse(403, {"error": "no"})
    )
    with pytest.raises(CellarionApiError) as excinfo:
        await client.get_cellars()
    assert not isinstance(excinfo.value, CellarionScopeError)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"id": "A"}, "A"),
        ({"user": {"id": "B"}}, "B"),
        ({"user": {"_id": "C"}}, "C"),
        ({"user": "not-a-dict"}, None),
        ({}, None),
    ],
)
async def test_get_account_id_shapes(body: dict[str, Any], expected: str | None) -> None:
    client, _ = token_client(FakeResponse(200, body))
    assert await client.get_account_id() == expected


# ── Login and token minting ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, CellarionAuthError),
        (403, CellarionAuthError),
        (429, CellarionApiError),
        (502, CellarionApiError),
    ],
)
async def test_login_status_mapping(status: int, expected: type[Exception]) -> None:
    client, _ = password_client(FakeResponse(status, {"error": "x"}))
    with pytest.raises(expected):
        await client.authenticate()


async def test_login_without_token_in_body() -> None:
    client, _ = password_client(FakeResponse(200, {"user": {}}))
    with pytest.raises(CellarionApiError, match="No token"):
        await client.authenticate()


async def test_login_connection_error() -> None:
    client, _ = password_client(aiohttp.ClientError("refused"))
    with pytest.raises(CellarionApiError, match="Connection failed"):
        await client.authenticate()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (FakeResponse(401), CellarionAuthError),
        (FakeResponse(403), CellarionAuthError),
        (FakeResponse(405), CellarionTokensNotSupported),
        (FakeResponse(429), CellarionApiError),
        (FakeResponse(500), CellarionApiError),
        (FakeResponse(307), CellarionApiError),
        (FakeResponse(201, {"id": "t1"}), CellarionApiError),  # no token in body
        (FakeResponse(201, content_type="text/html"), CellarionApiError),
        (aiohttp.ClientError("down"), CellarionApiError),
    ],
)
async def test_create_token_error_mapping(response: Any, expected: type[Exception]) -> None:
    client, session = password_client(FakeResponse(200, {"token": "jwt"}), response)
    with pytest.raises(expected):
        await client.async_create_api_token("HA", ["read"])
    if session.calls[1:]:
        # The credential-bearing POST never follows a redirect
        assert session.calls[1][2]["allow_redirects"] is False


async def test_create_token_needs_a_password() -> None:
    client, _ = token_client()
    with pytest.raises(CellarionApiError, match="Password login required"):
        await client.async_create_api_token("HA", ["read"])


async def test_create_token_success_returns_string() -> None:
    client, session = password_client(
        FakeResponse(200, {"token": "jwt"}), FakeResponse(201, {"token": "cel_new"})
    )
    assert await client.async_create_api_token("HA", ["read", "consume"]) == "cel_new"
    assert session.calls[1][2]["json"]["scopes"] == ["read", "consume"]


async def test_health_non_json_is_unreachable() -> None:
    client, _ = token_client(FakeResponse(200, content_type="text/html"))
    assert await client.get_health() == {"status": "unreachable"}


# ── Self-revocation ──────────────────────────────────────────────────


@pytest.mark.parametrize("status", [200, 401])
async def test_revoke_own_token_done_on_200_or_401(status: int) -> None:
    """200 = revoked now, 401 = already revoked; both mean the token is gone."""
    client, session = token_client(FakeResponse(status, {"message": "Token revoked"}))
    assert await client.revoke_own_token() is True
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("DELETE", f"{BASE_URL}/api/tokens/self")
    assert kwargs["headers"]["Authorization"] == f"Bearer {TEST_TOKEN}"


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(403, {"error": "scope"}),
        FakeResponse(404),
        FakeResponse(500),
        aiohttp.ClientError("down"),
    ],
)
async def test_revoke_own_token_is_best_effort(response: Any) -> None:
    """A pre-1.220 server (403), odd answers and outages mean 'maybe still valid'."""
    client, _ = token_client(response)
    assert await client.revoke_own_token() is False


async def test_revoke_own_token_needs_an_api_token() -> None:
    """A password-based client has no token of its own and sends nothing."""
    client, session = password_client()
    assert await client.revoke_own_token() is False
    assert session.calls == []
