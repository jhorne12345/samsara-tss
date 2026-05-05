"""Fleet status endpoint and dashboard HTML serving."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from tss.common.models import FleetStatusResponse
from tss.server.dispatcher import Dispatcher

router = APIRouter(tags=["fleet"])

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _disp(request: Request) -> Dispatcher:
    return request.app.state.dispatcher  # type: ignore[no-any-return]


@router.get("/api/fleet/status", response_model=FleetStatusResponse)
async def fleet_status(request: Request) -> FleetStatusResponse:
    d = _disp(request)
    return await d.snapshot_fleet()


@router.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")
