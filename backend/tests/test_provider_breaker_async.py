"""The provider circuit breaker must work without tornado installed.

Every provider request goes through ``BaseProvider._get_with_retry``, which
guards the call with a pybreaker circuit breaker. pybreaker's own
``call_async`` is Tornado-only -- it wraps the call in ``gen.coroutine``, and
``gen`` exists only if ``from tornado import gen`` succeeded at import time.

On a machine where something else has pulled tornado in, that works. On a
clean install of this project's requirements -- which is what the deployment
is -- ``gen`` is undefined and *every* provider request dies with
``NameError: name 'gen' is not defined``, surfacing to the user as "no data
found for this indicator".

These tests pin the fix: the breaker is driven explicitly, and pybreaker's
Tornado path is never reached.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pybreaker
import pytest

from backend.providers.base import (
    BaseProvider,
    _get_breaker,
    _provider_breakers,
    call_through_breaker,
)
from backend.utils.retry import DataNotAvailableError


class _Provider(BaseProvider):
    @property
    def provider_name(self) -> str:
        return "BREAKERTEST"

    async def _fetch_data(self, **params):  # pragma: no cover - unused
        pass


def _provider() -> _Provider:
    _provider_breakers.pop("BREAKERTEST", None)
    return _Provider(timeout=5.0)


def _response(status_code: int = 200, json_data=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = {}
    resp.text = ""
    resp.json.return_value = json_data or {"ok": True}
    resp.raise_for_status.return_value = None
    return resp


@pytest.fixture(autouse=True)
def no_tornado(monkeypatch):
    """Make pybreaker's Tornado path fail exactly as it does without tornado.

    This is the regression guard: if anything goes back to ``call_async``,
    these tests fail the way production did instead of passing locally because
    some unrelated package happens to provide tornado.
    """
    def _explode(*args, **kwargs):
        raise NameError("name 'gen' is not defined")

    monkeypatch.setattr(pybreaker.CircuitBreaker, "call_async", _explode)
    monkeypatch.setattr(pybreaker, "HAS_TORNADO_SUPPORT", False, raising=False)


# --------------------------------------------------------------------------
# The path that was broken in production
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_get_succeeds_without_tornado():
    provider = _provider()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _response()

    result = await provider._get_with_retry(client, "https://api.example.com/d")
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_a_post_succeeds_without_tornado():
    provider = _provider()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _response()

    result = await provider._post_with_retry(client, "https://api.example.com/d")
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_a_failure_is_not_reported_as_a_missing_name():
    """A real connection failure must read as a connection failure."""
    provider = _provider()
    provider.MAX_RETRIES = 1
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = httpx.ConnectError("connection refused")

    with pytest.raises(DataNotAvailableError) as excinfo:
        await provider._get_with_retry(client, "https://api.example.com/d")

    message = str(excinfo.value)
    assert "gen" not in message
    assert "Connection failed" in message


# --------------------------------------------------------------------------
# Breaker semantics are unchanged
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b_a_successful_call_leaves_the_breaker_closed():
    breaker = _get_breaker("BREAKERTEST")

    async def ok():
        return "value"

    assert await call_through_breaker(breaker, ok) == "value"
    assert breaker.current_state == pybreaker.STATE_CLOSED
    assert breaker.fail_counter == 0


@pytest.mark.asyncio
async def test_b_five_failures_open_the_breaker():
    _provider_breakers.pop("BREAKERTEST", None)
    breaker = _get_breaker("BREAKERTEST")

    async def boom():
        raise httpx.ConnectError("down")

    for _ in range(breaker.fail_max):
        with pytest.raises(httpx.ConnectError):
            await call_through_breaker(breaker, boom)

    assert breaker.current_state == pybreaker.STATE_OPEN


@pytest.mark.asyncio
async def test_b_an_open_breaker_fails_fast_without_calling_through():
    _provider_breakers.pop("BREAKERTEST", None)
    breaker = _get_breaker("BREAKERTEST")
    breaker.open()
    called = False

    async def should_not_run():
        nonlocal called
        called = True
        return "value"

    with pytest.raises(pybreaker.CircuitBreakerError):
        await call_through_breaker(breaker, should_not_run)
    assert called is False


@pytest.mark.asyncio
async def test_b_an_open_breaker_surfaces_as_provider_unavailable():
    provider = _provider()
    _get_breaker("BREAKERTEST").open()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _response()

    with pytest.raises(DataNotAvailableError, match="circuit breaker OPEN"):
        await provider._get_with_retry(client, "https://api.example.com/d")


@pytest.mark.asyncio
async def test_b_client_errors_do_not_trip_the_breaker():
    """A 404 means this request was wrong, not that the provider is down."""
    _provider_breakers.pop("BREAKERTEST", None)
    breaker = _get_breaker("BREAKERTEST")

    async def not_found():
        response = MagicMock(spec=httpx.Response)
        response.status_code = 404
        raise httpx.HTTPStatusError("404", request=MagicMock(), response=response)

    for _ in range(breaker.fail_max + 2):
        with pytest.raises(httpx.HTTPStatusError):
            await call_through_breaker(breaker, not_found)

    assert breaker.current_state == pybreaker.STATE_CLOSED


@pytest.mark.asyncio
async def test_c_the_call_is_awaited_inside_the_guard():
    """Regression: breaker.call() on a coroutine function would record success
    the moment the coroutine object was created, never seeing the failure."""
    _provider_breakers.pop("BREAKERTEST", None)
    breaker = _get_breaker("BREAKERTEST")
    started = datetime.now()

    async def fails_after_await():
        raise httpx.ReadTimeout("too slow")

    with pytest.raises(httpx.ReadTimeout):
        await call_through_breaker(breaker, fails_after_await)

    assert breaker.fail_counter == 1
    assert started <= datetime.now()
