"""The race-condition test: late result from a previously-offline agent must be rejected."""

from __future__ import annotations

import pytest

from tss.common.constants import HEARTBEAT_TIMEOUT_S
from tss.common.models import JobStatus
from tss.server.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_late_result_after_reassignment_returns_409(http_client_factory, fake_clock):
    """A claims J → A goes offline → J reassigned to B → A's late result arrives → 409.

    This is the classic race the epoch design guards against. If we get this
    wrong, B's correct result could be overwritten by A's stale one.
    """
    d = Dispatcher()
    async for c in http_client_factory(d):
        # Register A and B
        r = await c.post(
            "/api/agents/register",
            json={"name": "A", "capabilities": ["vehicle_gateway"]},
        )
        a_id = r.json()["agent_id"]
        await c.post(
            "/api/agents/register",
            json={"name": "B", "capabilities": ["vehicle_gateway"]},
        )

        await c.post("/api/jobs", json={"product": "vehicle_gateway", "duration_seconds": 5.0})

        # A claims at epoch 1
        r = await c.get(f"/api/agents/{a_id}/jobs/next")
        assert r.status_code == 200
        assn_a = r.json()
        assert assn_a["epoch"] == 1

        # A goes offline (silent death)
        fake_clock.advance(HEARTBEAT_TIMEOUT_S + 1.0)
        await d.reap_stale_agents()

        # B claims (after re-registering to bump it to IDLE in case it timed out too)
        # Actually B might also be offline now since it never heartbeated. Re-register.
        r = await c.post(
            "/api/agents/register",
            json={"name": "B", "capabilities": ["vehicle_gateway"]},
        )
        b_id = r.json()["agent_id"]
        r = await c.get(f"/api/agents/{b_id}/jobs/next")
        assert r.status_code == 200
        assn_b = r.json()
        assert assn_b["job_id"] == assn_a["job_id"]

        # A's network unblocks. It tries to post a result with its old (stale) epoch.
        r = await c.post(
            f"/api/agents/{a_id}/jobs/{assn_a['job_id']}/result",
            json={"epoch": assn_a["epoch"], "exit_code": 0, "duration_actual": 5.0},
        )
        assert r.status_code == 409, r.text

        # B's correct result is still accepted.
        r = await c.post(
            f"/api/agents/{b_id}/jobs/{assn_b['job_id']}/result",
            json={"epoch": assn_b["epoch"], "exit_code": 0, "duration_actual": 5.0},
        )
        assert r.status_code == 204

        # Job is COMPLETED, not failed.
        all_jobs = list(d.store)
        assert all_jobs[0].status == JobStatus.COMPLETED

        # The stale rejection should have been recorded as an event.
        kinds = [e.kind for e in all_jobs[0].history]
        assert "stale_result_rejected" in kinds
        break


@pytest.mark.asyncio
async def test_killed_agent_late_result_is_rejected(http_client_factory, fake_clock):
    """Operator-triggered kill (dashboard button) — the killed agent's epoch
    is bumped, so any late result from before the kill is 409."""
    d = Dispatcher()
    async for c in http_client_factory(d):
        r = await c.post(
            "/api/agents/register",
            json={"name": "A", "capabilities": ["vehicle_gateway"]},
        )
        a_id = r.json()["agent_id"]

        await c.post("/api/jobs", json={"product": "vehicle_gateway", "duration_seconds": 1.0})
        r = await c.get(f"/api/agents/{a_id}/jobs/next")
        assn = r.json()

        # Operator kills the agent
        r = await c.post(f"/api/agents/{a_id}/kill")
        assert r.status_code == 204

        # Late result is rejected
        r = await c.post(
            f"/api/agents/{a_id}/jobs/{assn['job_id']}/result",
            json={"epoch": assn["epoch"], "exit_code": 0, "duration_actual": 1.0},
        )
        assert r.status_code == 409
        break
