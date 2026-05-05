"""Shared constants for the Test Scheduling Service.

These values are tunable via environment variables in production but are kept
as module-level defaults so the dispatcher can be reasoned about and tested
without configuration plumbing.
"""

from __future__ import annotations

import os


def _f(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


def _i(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


HEARTBEAT_INTERVAL_S: float = _f("TSS_HEARTBEAT_INTERVAL_S", 2.0)
"""How often agents send heartbeats."""

HEARTBEAT_TIMEOUT_S: float = _f("TSS_HEARTBEAT_TIMEOUT_S", 6.0)
"""An agent that has not heartbeated in this many seconds is marked OFFLINE.
Default 6s = 3 missed heartbeats at the default 2s interval."""

WATCHDOG_INTERVAL_S: float = _f("TSS_WATCHDOG_INTERVAL_S", 1.0)
"""How often the watchdog scans the registry for stale agents."""

POLL_INTERVAL_S: float = _f("TSS_POLL_INTERVAL_S", 1.0)
"""How often agents poll the dispatcher for new jobs."""

JOB_MAX_ATTEMPTS: int = _i("TSS_JOB_MAX_ATTEMPTS", 3)
"""Maximum reassignment attempts before a job is marked FAILED."""

MAX_OVERRUN_FACTOR: float = _f("TSS_MAX_OVERRUN_FACTOR", 3.0)
"""A running job that exceeds duration_seconds * this factor is killed by the
watchdog and reassigned. Catches stuck-but-heartbeating agents."""

DEFAULT_PORT: int = _i("TSS_PORT", 8080)
DEFAULT_HOST: str = os.environ.get("TSS_HOST", "127.0.0.1")
