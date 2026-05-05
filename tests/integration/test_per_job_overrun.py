"""Per-job overrun: a stuck job whose agent is still heartbeating must be force-requeued.

This is the edge case AI-generated dispatchers tend to miss: agents that are
alive enough to send heartbeats but have wedged on a single job. Without an
overrun check, the job sits in RUNNING forever and the agent never frees up.
"""

from __future__ import annotations

import pytest

from tss.common.constants import MAX_OVERRUN_FACTOR
from tss.common.models import AgentStatus, JobStatus
from tss.server.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_overrunning_job_is_requeued_even_when_agent_heartbeats(fake_clock):
    d = Dispatcher()
    a = await d.register("A", ["vehicle_gateway"])
    job = await d.submit_job(product="vehicle_gateway", duration_seconds=2.0)
    assn = await d.claim_next_job(a.id)
    assert assn is not None

    # Advance past heartbeat-timeout-worth of time, but heartbeat each second to
    # keep the agent "alive". This isolates the overrun behavior from the
    # silent-death behavior.
    deadline = job.duration_seconds * MAX_OVERRUN_FACTOR
    elapsed = 0.0
    step = 1.0
    while elapsed < deadline + 1.0:
        fake_clock.advance(step)
        elapsed += step
        await d.heartbeat(a.id, epoch=assn.epoch)

    # Now the watchdog should detect the overrun and re-queue.
    await d.reap_stale_agents()
    refreshed = d.store.get(job.id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.QUEUED, "overrun job was not re-queued"

    # The agent should be IDLE again so it (or another agent) can pick up retries.
    agent = d.registry.get(a.id)
    assert agent is not None
    assert agent.status == AgentStatus.IDLE
    assert agent.current_job_id is None

    # The overrun must be visible as a JobEvent for the dashboard.
    kinds = [e.kind for e in refreshed.history]
    assert "overrun" in kinds


@pytest.mark.asyncio
async def test_running_job_below_overrun_factor_is_not_disturbed(fake_clock):
    d = Dispatcher()
    a = await d.register("A", ["vehicle_gateway"])
    await d.submit_job(product="vehicle_gateway", duration_seconds=10.0)
    assn = await d.claim_next_job(a.id)
    assert assn is not None

    # Advance only slightly past declared duration, well under the factor.
    fake_clock.advance(11.0)
    await d.heartbeat(a.id, epoch=assn.epoch)
    await d.reap_stale_agents()

    # Job should still be RUNNING.
    snap = await d.snapshot_fleet()
    assert snap.stats.jobs_running == 1
    assert snap.stats.queue_depth == 0
