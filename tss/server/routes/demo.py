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
import logging
import os
import random
import shutil
import signal
import sys
from random import choice
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from tss.server.dispatcher import Dispatcher

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/demo", tags=["demo"])


def _disp(request: Request) -> Dispatcher:
    return request.app.state.dispatcher  # type: ignore[no-any-return]


def _storm_state(request: Request) -> dict[str, Any]:
    """Per-app holder for the chaos-storm task + spawned-agent names."""
    state: dict[str, Any] | None = getattr(request.app.state, "chaos_storm", None)
    if state is None:
        state = {"running": False, "spawned": [], "drip_task": None}
        request.app.state.chaos_storm = state
    return state


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
    await d.clear_quarantine(agent.name)
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


# ===== Chaos storm — full-fleet failure-mode demo =====


class ChaosStormStatus(BaseModel):
    running: bool
    spawned: list[str]


async def _drip_jobs(dispatcher: Dispatcher) -> None:
    """Continuously submit jobs while the chaos storm is active.

    Cancels cleanly when the task is stopped. Errors are logged but never
    propagated — a transient submit failure shouldn't kill the drip loop.
    """
    products = ["vehicle_gateway", "asset_gateway", "dashcam"]
    while True:
        try:
            await dispatcher.submit_job(
                product=random.choice(products),
                duration_seconds=random.uniform(3.0, 9.0),
                submitter="chaos-storm",
                max_attempts=3,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover — best-effort
            log.warning("chaos-storm drip submit failed: %s", e)
        await asyncio.sleep(random.uniform(1.2, 2.8))


@router.post("/chaos-storm/start", response_model=ChaosStormStatus)
async def start_chaos_storm(request: Request) -> ChaosStormStatus:
    """Spawn 8 mixed-profile agents and start a job-drip loop.

    The mix exercises every chaos profile (stable / flaky / crashy / doomed)
    so the dashboard's events feed lights up with the full menu of failure
    modes the brief calls out: silent_death, partition, job_crash, slow_exec.
    """
    state = _storm_state(request)
    if state["running"]:
        raise HTTPException(status_code=409, detail="chaos storm already running")

    profiles = ["stable", "stable", "flaky", "flaky", "crashy", "crashy", "doomed", "doomed"]
    products = ["vehicle_gateway", "asset_gateway", "dashcam"]
    spawned: list[str] = []
    for i, profile in enumerate(profiles):
        caps = [products[i % len(products)]]
        if random.random() < 0.35:
            other = random.choice([p for p in products if p not in caps])
            caps.append(other)
        try:
            result = await spawn_agent(
                SpawnAgentRequest(
                    name=f"storm-{i:02d}",
                    capabilities=caps,
                    profile=profile,
                ),
                request,
            )
            spawned.append(result.name)
        except Exception as e:  # pragma: no cover — best-effort spawn
            log.warning("chaos-storm spawn failed for storm-%02d: %s", i, e)

    state["spawned"] = spawned
    state["drip_task"] = asyncio.create_task(_drip_jobs(_disp(request)))
    state["running"] = True
    return ChaosStormStatus(running=True, spawned=spawned)


@router.post("/chaos-storm/stop", response_model=ChaosStormStatus)
async def stop_chaos_storm(request: Request) -> ChaosStormStatus:
    """Stop the drip loop and SIGTERM all storm-spawned agent subprocesses."""
    state = _storm_state(request)
    task = state.get("drip_task")
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        state["drip_task"] = None

    spawned_names = set(state.get("spawned", []))
    pids = _state_pids(request)
    for pid in [p for p, n in pids.items() if n in spawned_names]:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        pids.pop(pid, None)
    state["spawned"] = []
    state["running"] = False
    return ChaosStormStatus(running=False, spawned=[])


@router.get("/chaos-storm", response_model=ChaosStormStatus)
async def chaos_storm_status(request: Request) -> ChaosStormStatus:
    state = _storm_state(request)
    return ChaosStormStatus(running=bool(state["running"]), spawned=list(state["spawned"]))
