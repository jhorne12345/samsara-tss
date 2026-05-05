"""Shared test fixtures.

Uses a fake clock so heartbeat-timeout tests don't depend on wall time. The
fake clock is the same one ``tss.common.clock`` uses, so the dispatcher's
production code is exercised verbatim — there are no test-only branches.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from tss.common import clock
from tss.server.app import create_app
from tss.server.dispatcher import Dispatcher


class FakeClock:
    """Manually-advanced clock that satisfies the ``tss.common.clock`` interface.

    Both monotonic seconds and UTC datetime move together when ``advance`` is
    called, so a single ``advance(seconds)`` controls every time read in the
    dispatcher.
    """

    def __init__(self, *, mono_start: float = 0.0, utc_start: datetime | None = None) -> None:
        self._mono = mono_start
        self._utc = utc_start or datetime(2026, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return self._mono

    def utcnow(self) -> datetime:
        return self._utc

    def advance(self, seconds: float) -> None:
        self._mono += seconds
        self._utc += timedelta(seconds=seconds)


@pytest.fixture
def fake_clock() -> AsyncIterator[FakeClock]:
    fc = FakeClock()
    restore = clock.install_fake_clock(fc.monotonic, fc.utcnow)
    try:
        yield fc
    finally:
        restore()


@pytest.fixture
def dispatcher() -> Dispatcher:
    """A dispatcher with default settings; tests with a fake clock should
    request the fake_clock fixture *before* this so the clock is patched
    when the dispatcher reads time."""
    return Dispatcher()


@pytest_asyncio.fixture
async def http_client_factory() -> Callable[[Dispatcher], AsyncIterator[httpx.AsyncClient]]:
    """Returns an async context manager that yields an httpx AsyncClient
    bound to a fresh FastAPI app with the given dispatcher.

    We disable the watchdog by default so tests can drive ``reap_stale_agents``
    explicitly under a fake clock.
    """

    async def make(d: Dispatcher) -> AsyncIterator[httpx.AsyncClient]:
        app = create_app(dispatcher=d, start_watchdog=False)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    return make


@pytest.fixture
def test_client_factory() -> Callable[[Dispatcher], TestClient]:
    """Sync TestClient factory for tests that don't need async."""

    def make(d: Dispatcher) -> TestClient:
        app = create_app(dispatcher=d, start_watchdog=False)
        return TestClient(app)

    return make
