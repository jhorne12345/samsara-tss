"""Atomic-claim test: N agents poll simultaneously for 1 job. Exactly one wins."""

from __future__ import annotations

import asyncio

import pytest

from tss.server.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_two_agents_one_job_exactly_one_wins(fake_clock):
    """Direct dispatcher-level test: two coroutines, one job, gather concurrently."""
    d = Dispatcher()
    a = await d.register("A", ["vehicle_gateway"])
    b = await d.register("B", ["vehicle_gateway"])
    await d.submit_job(product="vehicle_gateway", duration_seconds=1.0)

    results = await asyncio.gather(
        d.claim_next_job(a.id),
        d.claim_next_job(b.id),
    )
    won = [r for r in results if r is not None]
    assert len(won) == 1, f"expected exactly one winner, got {len(won)}"


@pytest.mark.asyncio
async def test_ten_agents_three_jobs_no_double_claim(fake_clock):
    """Stress: 10 agents and 3 jobs polling concurrently. Exactly 3 winners."""
    d = Dispatcher()
    agent_ids = []
    for i in range(10):
        a = await d.register(f"a-{i}", ["vehicle_gateway"])
        agent_ids.append(a.id)
    for _ in range(3):
        await d.submit_job(product="vehicle_gateway", duration_seconds=1.0)

    results = await asyncio.gather(*(d.claim_next_job(aid) for aid in agent_ids))
    won = [r for r in results if r is not None]
    job_ids = [r.job_id for r in won]
    assert len(won) == 3
    assert len(set(job_ids)) == 3, "duplicate job_ids — claim was not atomic"


@pytest.mark.asyncio
async def test_concurrent_claim_via_http(http_client_factory, fake_clock):
    """End-to-end: two clients calling /jobs/next at once via httpx + ASGI."""
    d = Dispatcher()
    async for c in http_client_factory(d):
        r1 = await c.post(
            "/api/agents/register",
            json={"name": "A", "capabilities": ["vehicle_gateway"]},
        )
        r2 = await c.post(
            "/api/agents/register",
            json={"name": "B", "capabilities": ["vehicle_gateway"]},
        )
        a_id = r1.json()["agent_id"]
        b_id = r2.json()["agent_id"]
        await c.post("/api/jobs", json={"product": "vehicle_gateway", "duration_seconds": 1.0})

        results = await asyncio.gather(
            c.get(f"/api/agents/{a_id}/jobs/next"),
            c.get(f"/api/agents/{b_id}/jobs/next"),
        )
        statuses = sorted(r.status_code for r in results)
        # Exactly one 200 (winner) and one 204 (no job available now).
        assert statuses == [200, 204], f"unexpected statuses {statuses}"
        break
