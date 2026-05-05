"""Clock wrapper for testability.

The dispatcher's correctness depends on time arithmetic for heartbeat timeouts
and job overrun detection. We funnel every time read through these functions
so tests can install a fake clock without monkey-patching the standard library.

Use ``monotonic()`` for any duration math (timeouts, intervals).
Use ``utcnow()`` for human-facing timestamps that go into payloads / logs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime


def _real_utcnow() -> datetime:
    return datetime.now(UTC)


_monotonic: Callable[[], float] = time.monotonic
_utcnow: Callable[[], datetime] = _real_utcnow


def monotonic() -> float:
    """Monotonic seconds since process start. Use for duration math only."""
    return _monotonic()


def utcnow() -> datetime:
    """Current UTC datetime. Use for human-facing timestamps only."""
    return _utcnow()


def install_fake_clock(
    monotonic_fn: Callable[[], float],
    utcnow_fn: Callable[[], datetime],
) -> Callable[[], None]:
    """Install fake clock implementations. Returns a function that restores the real clock."""
    global _monotonic, _utcnow
    saved_mono, saved_utc = _monotonic, _utcnow
    _monotonic = monotonic_fn
    _utcnow = utcnow_fn

    def restore() -> None:
        global _monotonic, _utcnow
        _monotonic = saved_mono
        _utcnow = saved_utc

    return restore
