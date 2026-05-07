"""Demo-only routes — orchestrate local agent processes for live demos.

These endpoints exist to make it easy to demonstrate the dispatcher's
failure-handling without hopping into a second terminal. They spawn agent
subprocesses on the *server host*, which is fine for the assessment demo
but obviously not for a real deployment — hence the ``/api/demo`` prefix.

The dispatcher tracks spawned PIDs on ``app.state.demo_pids`` so the same
process is the source of truth for cleanup.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from random import choice

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from tss.server.dispatcher import Dispatcher

router = APIRouter(prefix="/api/demo", tags=["demo"])


def _disp(request: Request) -> Dispatcher:
    return request.app.state.dispatcher  # type: ignore[no-any-return]


class SpawnAgentRequest(BaseModel):
    name: str | None = None
    """If unset, a random name like ``demo-7f2a`` is generated."""

    capabilities: list[str] = Field(default_factory=lambda: ["vehicle_gateway"])
    profile: str = "stable"
    """Chaos profile: stable | flaky | crashy | doomed."""


class SpawnAgentResponse(BaseModel):
    name: str
    pid: int
    capabilities: list[str]


def _state_pids(request: Request) -> dict[int, str]:
    pids: dict[int, str] | None = getattr(request.app.state, "demo_pids", None)
    if pids is None:
        pids = {}
        request.app.state.demo_pids = pids
    return pids


def _tss_executable() -> str:
    """Find the ``tss`` CLI in the same env as the dispatcher.

    Prefers a sibling of ``sys.executable`` (``.venv/bin/tss``) so a
    custom virtualenv is honored. Falls back to PATH lookup.
    """
    here = os.path.dirname(sys.executable)
    candidate = os.path.join(here, "tss")
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which("tss")
    if found is None:
        raise RuntimeError("could not locate `tss` CLI on PATH or alongside the dispatcher python")
    return found


@router.post("/agents/spawn", response_model=SpawnAgentResponse, status_code=status.HTTP_201_CREATED)
async def spawn_agent(req: SpawnAgentRequest, request: Request) -> SpawnAgentResponse:
    """Launch a local ``tss agent`` subprocess that registers with this dispatcher."""
    name = req.name or f"demo-{os.urandom(2).hex()}"
    if not req.capabilities:
        raise HTTPException(status_code=400, detail="at least one capability required")

    # Address the dispatcher on loopback. The agent binds back via HTTP so
    # using 127.0.0.1 + the running port is fine.
    server = request.url.scheme + "://127.0.0.1:" + str(request.url.port or 8080)

    cmd = [
        _tss_executable(),
        "agent",
        "--name", name,
        "--caps", ",".join(req.capabilities),
        "--dispatcher", server,
        "--profile", req.profile,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    _state_pids(request)[proc.pid] = name
    return SpawnAgentResponse(name=name, pid=proc.pid, capabilities=req.capabilities)


@router.post("/agents/{agent_id}/revive", status_code=status.HTTP_204_NO_CONTENT)
async def revive_agent(agent_id: UUID, request: Request) -> None:
    """Clear an agent's kill quarantine so the runner's next register
    attempt succeeds immediately. Used during demos to show the epoch
    increment when an operator-killed testbed comes back online."""
    d = _disp(request)
    agent = d.registry.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent={agent_id} not found")
    # Clear quarantine under the lock so a concurrent register sees the
    # cleared state on its next pass.
    async with d._lock:  # noqa: SLF001 — dispatcher exposes this for demo use
        d._quarantined_names.pop(agent.name, None)  # noqa: SLF001
    return None


@router.post("/agents/spawn-random", response_model=SpawnAgentResponse, status_code=status.HTTP_201_CREATED)
async def spawn_random_agent(request: Request) -> SpawnAgentResponse:
    """Convenience: spawn an agent with a random capability mix."""
    products = ["vehicle_gateway", "asset_gateway", "dashcam"]
    caps = [choice(products)]
    if len(caps) < 2 and choice([True, False]):
        # ~50% combo agents
        other = choice([p for p in products if p not in caps])
        caps.append(other)
    return await spawn_agent(
        SpawnAgentRequest(capabilities=caps, profile="stable"),
        request,
    )
