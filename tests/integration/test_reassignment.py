"""The resiliency story: agent claims, falls offline, watchdog reassigns to another agent."""

from __future__ import annotations

import pytest

from tss.common.constants import HEARTBEAT_TIMEOUT_S
from tss.common.models import JobStatus
from tss.server.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_silent_death_triggers_reassignment(http_client_factory, fake_clock):
    d = Dispatcher()
    async for c in http_client_factory(d):
        # Two agents, one job
        r = await c.post(
            "/api/agents/register",
            json={"name": "A", "capabilities": ["vehicle_gateway"]},
        )
        a_id = r.json()["agent_id"]
        r = await c.post(
            "/api/agents/register",
            json={"name": "B", "capabilities": ["vehicle_gateway"]},
        )
        b_id = r.json()["agent_id"]

        await c.post("/api/jobs", json={"product": "vehicle_gateway", "duration_seconds": 5.0})

        # A claims
        r = await c.get(f"/api/agents/{a_id}/jobs/next")
        assert r.status_code == 200
        assn_a = r.json()

        # Fast-forward past heartbeat timeout
        fake_clock.advance(HEARTBEAT_TIMEOUT_S + 1.0)
        offlined = await d.reap_stale_agents()
        assert len(offlined) == 2  # B never heartbeated either, but only A had a job

        # The job should now be QUEUED again with attempt_count=1
        snap = await d.snapshot_fleet()
        assert snap.stats.queue_depth == 1
        assert snap.stats.offline == 2

        # B re-registers and claims
        r = await c.post(
            "/api/agents/register",
            json={"name": "B", "capabilities": ["vehicle_gateway"]},
        )
        b_id_2 = r.json()["agent_id"]
        assert b_id_2 == b_id  # same id, bumped epoch
        r = await c.get(f"/api/agents/{b_id_2}/jobs/next")
        assert r.status_code == 200, r.text
        assn_b = r.json()
        assert assn_b["job_id"] == assn_a["job_id"]

        # B completes the job
        r = await c.post(
            f"/api/agents/{b_id_2}/jobs/{assn_b['job_id']}/result",
            json={"epoch": assn_b["epoch"], "exit_code": 0, "duration_actual": 5.0},
        )
        assert r.status_code == 204

        snap = await d.snapshot_fleet()
        assert snap.stats.jobs_completed == 1
        assert snap.stats.queue_depth == 0
        break


@pytest.mark.asyncio
async def test_max_attempts_exhausted_marks_failed(http_client_factory, fake_clock):
    """Three reassignments in a row exhaust max_attempts=3 and the job goes to FAILED.

    Each round registers a fresh agent so we don't have to juggle heartbeats
    for non-claiming agents under a fake clock.
    """
    d = Dispatcher()
    async for c in http_client_factory(d):
        await c.post(
            "/api/jobs",
            json={
                "product": "vehicle_gateway",
                "duration_seconds": 1.0,
                "max_attempts": 3,
            },
        )

        for round_idx in range(3):
            r = await c.post(
                "/api/agents/register",
                json={
                    "name": f"agent-round-{round_idx}",
                    "capabilities": ["vehicle_gateway"],
                },
            )
            agent_id = r.json()["agent_id"]
            r = await c.get(f"/api/agents/{agent_id}/jobs/next")
            assert r.status_code == 200
            fake_clock.advance(HEARTBEAT_TIMEOUT_S + 1.0)
            await d.reap_stale_agents()

        # After three reassignments, the job must be FAILED.
        all_jobs = list(d.store)
        assert len(all_jobs) == 1
        assert all_jobs[0].status == JobStatus.FAILED
        assert all_jobs[0].attempt_count == 3
        break
