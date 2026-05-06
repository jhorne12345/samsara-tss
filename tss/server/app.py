"""FastAPI app factory.

Wires the dispatcher, watchdog, routes, and static files together. The
dispatcher and watchdog are owned by ``app.state`` so route handlers and
tests can inject custom instances without monkey-patching.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from tss.server.dispatcher import Dispatcher
from tss.server.routes import agents as agents_routes
from tss.server.routes import fleet as fleet_routes
from tss.server.routes import jobs as jobs_routes
from tss.server.sqlite_store import SQLiteJobStore
from tss.server.watchdog import Watchdog

log = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    *,
    dispatcher: Dispatcher | None = None,
    start_watchdog: bool = True,
    db_path: str | None = None,
) -> FastAPI:
    """Build a FastAPI app. Pass ``start_watchdog=False`` in tests where you
    want full control over watchdog ticks."""
    if dispatcher is None:
        path = db_path or os.environ.get("TSS_DB_PATH", "./tss.db")
        dispatcher = Dispatcher(store=SQLiteJobStore(path))
    disp = dispatcher
    watchdog = Watchdog(disp)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if start_watchdog:
            await watchdog.start()
        try:
            yield
        finally:
            await watchdog.stop()

    app = FastAPI(
        title="Samsara Test Scheduling Service",
        description="Manages a fleet of HIL test agents (testbeds) for firmware validation.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.dispatcher = disp
    app.state.watchdog = watchdog

    app.include_router(agents_routes.router)
    app.include_router(jobs_routes.router)
    app.include_router(fleet_routes.router)

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app


# Default app for `uvicorn tss.server.app:app`
app = create_app()
