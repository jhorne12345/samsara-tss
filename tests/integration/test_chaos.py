"""End-to-end chaos integration test.

Spins up a real dispatcher (uvicorn-style ASGI), real mock-agent loops via
``run_one_agent`` connected through a shared httpx ASGI transport, submits a
batch of jobs, lets the system run, and asserts every job reaches a terminal
state.

Marked ``chaos`` so ``pytest -m "not chaos"`` skips it during fast development.
Run with ``pytest -m chaos`` (or ``make test-chaos``).
"""

from __future__ import annotations

import asyncio
import random

import httpx
import pytest

from tss.agent.chaos import PROFILES, ChaosProfile
from tss.agent.runner import AgentRunner
from tss.common.models import JobStatus
from tss.server.app import create_app
from tss.server.dispatcher import Dispatcher

pytestmark = pytest.mark.chaos


async def _spawn_agent(
    *,
    name: str,
    caps: list[str],
    profile: ChaosProfile,
    transport: httpx.ASGITransport,
    seed: int,
) -> tuple[asyncio.Task[None], AgentRunner]:
    runner = AgentRunner(
        name=name,
        capabilities=caps,
        dispatcher_url="http://test",
        chaos=profile,
        rng=random.Random(seed),
    )
    runner._client = httpx.AsyncClient(transport=transport, base_url="http://test", timeout=5.0)
    runner._stop_event = asyncio.Event()
    await runner.register()
    task = asyncio.create_task(runner.run(), name=f"agent-{name}")
    return task, runner


@pytest.mark.asyncio
async def test_chaos_run_all_jobs_reach_terminal_state():
    d = Dispatcher(
        heartbeat_interval_s=0.5,
        heartbeat_timeout_s=2.0,
        poll_interval_s=0.3,
    )
    app = create_app(dispatcher=d, start_watchdog=True)
    transport = httpx.ASGITransport(app=app)

    # Drive the lifespan manually so the watchdog actually runs.
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ops:
        # Launch the lifespan via a background task that holds the app alive.
        lifespan_task = asyncio.create_task(_drive_lifespan(app))
        await asyncio.sleep(0.1)

        # 8 agents across the chaos profiles
        agent_tasks: list[asyncio.Task[None]] = []
        runners: list[AgentRunner] = []
        seed = 12345
        rng = random.Random(seed)
        profiles_pool = [
            "stable",
            "stable",
            "flaky",
            "flaky",
            "crashy",
            "crashy",
            "doomed",
            "doomed",
        ]
        rng.shuffle(profiles_pool)
        for i, profile_name in enumerate(profiles_pool):
            caps = ["vehicle_gateway"] if i % 2 == 0 else ["asset_gateway"]
            t, runner = await _spawn_agent(
                name=f"chaos-{i:02d}",
                caps=caps,
                profile=PROFILES[profile_name],
                transport=transport,
                seed=rng.randint(0, 2**31 - 1),
            )
            agent_tasks.append(t)
            runners.append(runner)

        # Submit 24 jobs across both products
        for i in range(24):
            product = "vehicle_gateway" if i % 2 == 0 else "asset_gateway"
            r = await ops.post(
                "/api/jobs",
                json={
                    "product": product,
                    "duration_seconds": rng.uniform(0.3, 1.0),
                    "max_attempts": 5,
                },
            )
            assert r.status_code == 201

        # Run for a bounded time, checking periodically.
        deadline = 30.0
        elapsed = 0.0
        terminal = False
        while elapsed < deadline:
            await asyncio.sleep(1.0)
            elapsed += 1.0
            r = await ops.get("/api/fleet/status")
            stats = r.json()["stats"]
            in_flight = stats["queue_depth"] + stats["jobs_running"]
            if in_flight == 0:
                terminal = True
                break

        # Stop agents and lifespan
        for runner in runners:
            await runner.stop()
        for t in agent_tasks:
            t.cancel()
        for t in agent_tasks:
            try:
                await t
            except (asyncio.CancelledError, BaseExceptionGroup, Exception):
                pass
        for runner in runners:
            if runner._client is not None:
                await runner._client.aclose()
        lifespan_task.cancel()
        try:
            await lifespan_task
        except asyncio.CancelledError:
            pass

        assert terminal, f"jobs did not all terminate within {deadline}s; final stats={stats}"

        # Verify every job is in a terminal state
        for job in d.store:
            assert job.status in (JobStatus.COMPLETED, JobStatus.FAILED), (
                f"job {job.id} stuck in {job.status}"
            )

        # Chaos must have actually happened: at least one reassignment event.
        all_event_kinds = [e.kind for j in d.store for e in j.history]
        assert (
            "reassigned" in all_event_kinds
            or "overrun" in all_event_kinds
            or any(k == "stale_result_rejected" for k in all_event_kinds)
        ), f"no chaos events observed; events={all_event_kinds}"


async def _drive_lifespan(app):
    """Run the FastAPI lifespan context to keep the watchdog alive."""
    async with app.router.lifespan_context(app):
        try:
            await asyncio.Event().wait()  # block until cancelled
        except asyncio.CancelledError:
            raise
