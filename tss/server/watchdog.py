"""Heartbeat watchdog — async background task that drives reassignment.

Periodically calls ``Dispatcher.reap_stale_agents`` which performs the actual
state mutation under the dispatcher's lock. This module is just the loop and
exception/cancellation handling; the correctness lives in the dispatcher.

The watchdog is started by the FastAPI lifespan and stopped at shutdown.
"""

from __future__ import annotations

import asyncio
import logging

from tss.common.constants import WATCHDOG_INTERVAL_S
from tss.server.dispatcher import Dispatcher

log = logging.getLogger(__name__)


class Watchdog:
    def __init__(self, dispatcher: Dispatcher, *, interval_s: float = WATCHDOG_INTERVAL_S) -> None:
        self._dispatcher = dispatcher
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="tss-watchdog")
        log.info("watchdog started interval=%ss", self._interval_s)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        log.info("watchdog stopped")

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._dispatcher.reap_stale_agents()
            except Exception:
                # Never let a single tick exception kill the watchdog loop.
                log.exception("watchdog tick failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_s)
            except TimeoutError:
                continue
            else:
                # stop_event was set
                break
