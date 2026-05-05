"""Unit tests for the dispatcher core: matching, claims, lifecycle."""

from __future__ import annotations

import pytest

from tss.common.models import AgentStatus, JobStatus
from tss.server.dispatcher import Dispatcher
from tss.server.errors import (
    AgentNotIdleError,
    StaleEpochError,
    UnknownAgentError,
)


@pytest.mark.asyncio
async def test_register_new_agent(fake_clock):
    d = Dispatcher()
    a = await d.register("vg-01", ["vehicle_gateway"])
    assert a.epoch == 1
    assert a.status == AgentStatus.IDLE
    assert a.capabilities == ["vehicle_gateway"]


@pytest.mark.asyncio
async def test_reregister_bumps_epoch_and_clears_state(fake_clock):
    d = Dispatcher()
    a1 = await d.register("vg-01", ["vehicle_gateway"])
    # Mark busy as if a job was claimed
    a1.status = AgentStatus.BUSY
    a1.current_job_id = a1.id  # any UUID will do

    a2 = await d.register("vg-01", ["vehicle_gateway", "asset_gateway"])
    assert a2.id == a1.id  # same agent record
    assert a2.epoch == 2
    assert a2.status == AgentStatus.IDLE
    assert a2.current_job_id is None
    assert "asset_gateway" in a2.capabilities


@pytest.mark.asyncio
async def test_heartbeat_stale_epoch_raises(fake_clock):
    d = Dispatcher()
    a = await d.register("vg-01", ["vehicle_gateway"])
    with pytest.raises(StaleEpochError):
        await d.heartbeat(a.id, epoch=999)


@pytest.mark.asyncio
async def test_heartbeat_unknown_agent_raises(fake_clock):
    d = Dispatcher()
    from uuid import uuid4

    with pytest.raises(UnknownAgentError):
        await d.heartbeat(uuid4(), epoch=1)


@pytest.mark.asyncio
async def test_capability_matching_picks_compatible_agent(fake_clock):
    """An agent with matching capability gets the job; non-matching is skipped."""
    d = Dispatcher()
    vg = await d.register("vg-01", ["vehicle_gateway"])
    ag = await d.register("ag-01", ["asset_gateway"])

    # Submit only an asset_gateway job. vg-01 should not claim it.
    await d.submit_job(product="asset_gateway", duration_seconds=1.0)
    assn_vg = await d.claim_next_job(vg.id)
    assert assn_vg is None  # vg-01 has no matching jobs
    assn_ag = await d.claim_next_job(ag.id)
    assert assn_ag is not None
    assert assn_ag.product == "asset_gateway"


@pytest.mark.asyncio
async def test_combo_agent_can_claim_either_product(fake_clock):
    d = Dispatcher()
    combo = await d.register("combo", ["vehicle_gateway", "asset_gateway"])
    await d.submit_job(product="vehicle_gateway", duration_seconds=1.0)
    await d.submit_job(product="asset_gateway", duration_seconds=1.0)

    a1 = await d.claim_next_job(combo.id)
    assert a1 is not None
    # Free up combo by reporting a result, then claim again
    await d.report_result(
        agent_id=combo.id, job_id=a1.job_id, epoch=a1.epoch, exit_code=0, duration_actual=1.0
    )
    a2 = await d.claim_next_job(combo.id)
    assert a2 is not None
    assert {a1.product, a2.product} == {"vehicle_gateway", "asset_gateway"}


@pytest.mark.asyncio
async def test_busy_agent_cannot_claim_again(fake_clock):
    d = Dispatcher()
    a = await d.register("vg-01", ["vehicle_gateway"])
    await d.submit_job(product="vehicle_gateway", duration_seconds=1.0)
    await d.claim_next_job(a.id)
    with pytest.raises(AgentNotIdleError):
        await d.claim_next_job(a.id)


@pytest.mark.asyncio
async def test_no_agents_supporting_product_keeps_job_queued(fake_clock):
    d = Dispatcher()
    a = await d.register("vg-01", ["vehicle_gateway"])
    await d.submit_job(product="UNSUPPORTED_PRODUCT", duration_seconds=1.0)
    assn = await d.claim_next_job(a.id)
    assert assn is None
    snap = await d.snapshot_fleet()
    assert snap.stats.queue_depth == 1


@pytest.mark.asyncio
async def test_completed_job_frees_agent(fake_clock):
    d = Dispatcher()
    a = await d.register("vg-01", ["vehicle_gateway"])
    await d.submit_job(product="vehicle_gateway", duration_seconds=1.0)
    assn = await d.claim_next_job(a.id)
    assert assn is not None
    await d.report_result(
        agent_id=a.id, job_id=assn.job_id, epoch=assn.epoch, exit_code=0, duration_actual=1.0
    )
    snap = await d.snapshot_fleet()
    assert snap.stats.idle == 1
    assert snap.stats.busy == 0
    assert snap.stats.jobs_completed == 1


@pytest.mark.asyncio
async def test_failing_exit_code_under_max_attempts_requeues(fake_clock):
    d = Dispatcher()
    a = await d.register("vg-01", ["vehicle_gateway"])
    job = await d.submit_job(product="vehicle_gateway", duration_seconds=1.0, max_attempts=3)
    assn = await d.claim_next_job(a.id)
    assert assn is not None
    await d.report_result(
        agent_id=a.id, job_id=assn.job_id, epoch=assn.epoch, exit_code=1, duration_actual=1.0
    )
    snap = await d.snapshot_fleet()
    assert snap.stats.queue_depth == 1
    assert snap.stats.jobs_failed == 0
    refreshed = d.store.get(job.id)
    assert refreshed is not None
    assert refreshed.attempt_count == 1
    assert refreshed.status == JobStatus.QUEUED


@pytest.mark.asyncio
async def test_failing_exit_code_at_max_attempts_marks_failed(fake_clock):
    d = Dispatcher()
    a = await d.register("vg-01", ["vehicle_gateway"])
    job = await d.submit_job(product="vehicle_gateway", duration_seconds=1.0, max_attempts=2)
    for _ in range(2):
        assn = await d.claim_next_job(a.id)
        assert assn is not None
        await d.report_result(
            agent_id=a.id,
            job_id=assn.job_id,
            epoch=assn.epoch,
            exit_code=1,
            duration_actual=1.0,
        )
    refreshed = d.store.get(job.id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.FAILED
    assert refreshed.attempt_count == 2


@pytest.mark.asyncio
async def test_stale_result_rejected_after_reregister(fake_clock):
    """The hard race: agent A claims, gets pruned, re-registers, then a late
    result for the original claim arrives. Must be rejected."""
    d = Dispatcher()
    a = await d.register("vg-01", ["vehicle_gateway"])
    await d.submit_job(product="vehicle_gateway", duration_seconds=1.0)
    assn = await d.claim_next_job(a.id)
    assert assn is not None
    old_epoch = assn.epoch
    # Re-register bumps epoch to 2; assn.epoch is still 1.
    await d.register("vg-01", ["vehicle_gateway"])
    with pytest.raises(StaleEpochError):
        await d.report_result(
            agent_id=a.id,
            job_id=assn.job_id,
            epoch=old_epoch,
            exit_code=0,
            duration_actual=1.0,
        )


@pytest.mark.asyncio
async def test_force_kill_marks_offline_and_requeues(fake_clock):
    d = Dispatcher()
    a = await d.register("vg-01", ["vehicle_gateway"])
    job = await d.submit_job(product="vehicle_gateway", duration_seconds=1.0)
    assn = await d.claim_next_job(a.id)
    assert assn is not None

    await d.force_kill_agent(a.id)
    snap = await d.snapshot_fleet()
    assert snap.stats.offline == 1
    assert snap.stats.queue_depth == 1
    refreshed = d.store.get(job.id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.QUEUED


@pytest.mark.asyncio
async def test_late_result_after_kill_is_rejected(fake_clock):
    d = Dispatcher()
    a = await d.register("vg-01", ["vehicle_gateway"])
    await d.submit_job(product="vehicle_gateway", duration_seconds=1.0)
    assn = await d.claim_next_job(a.id)
    assert assn is not None
    await d.force_kill_agent(a.id)
    with pytest.raises(StaleEpochError):
        await d.report_result(
            agent_id=a.id,
            job_id=assn.job_id,
            epoch=assn.epoch,
            exit_code=0,
            duration_actual=1.0,
        )
