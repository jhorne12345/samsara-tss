"""Integration tests for ``Agent.epoch_history`` and per-epoch counters.

The epoch history is what powers the agent slide-over panel's "epoch bands"
visualization: each (re-)registration is one band, with a reason for ending
and per-epoch jobs_claimed/completed/failed counts.
"""

from __future__ import annotations

import pytest

from tss.common.models import AgentStatus
from tss.server.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_fresh_agent_has_no_epoch_history(dispatcher: Dispatcher) -> None:
    agent = await dispatcher.register("vg-01", ["vehicle_gateway"])
    assert agent.epoch_history == []
    assert agent.epoch == 1
    assert agent.epoch_started_at is not None
    assert agent.jobs_claimed == 0


@pytest.mark.asyncio
async def test_per_epoch_counters_increment_on_claim_and_complete(
    dispatcher: Dispatcher,
) -> None:
    agent = await dispatcher.register("vg-01", ["vehicle_gateway"])
    job = await dispatcher.submit_job(
        product="vehicle_gateway", duration_seconds=2.0, submitter="alice",
    )
    assignment = await dispatcher.claim_next_job(agent.id)
    assert assignment is not None

    fresh = dispatcher.registry.get(agent.id)
    assert fresh is not None
    assert fresh.jobs_claimed == 1
    assert fresh.jobs_completed == 0

    await dispatcher.report_result(
        agent_id=agent.id,
        job_id=job.id,
        epoch=assignment.epoch,
        exit_code=0,
        duration_actual=1.5,
    )
    fresh = dispatcher.registry.get(agent.id)
    assert fresh is not None
    assert fresh.jobs_completed == 1


@pytest.mark.asyncio
async def test_re_register_captures_previous_epoch_summary(
    dispatcher: Dispatcher,
) -> None:
    agent = await dispatcher.register("vg-01", ["vehicle_gateway"])
    job = await dispatcher.submit_job(
        product="vehicle_gateway", duration_seconds=2.0, submitter="alice",
    )
    assignment = await dispatcher.claim_next_job(agent.id)
    assert assignment is not None
    await dispatcher.report_result(
        agent_id=agent.id,
        job_id=job.id,
        epoch=assignment.epoch,
        exit_code=0,
        duration_actual=1.5,
    )
    # Re-register the agent under the same name (fresh process).
    re_registered = await dispatcher.register("vg-01", ["vehicle_gateway"])
    assert re_registered.id == agent.id
    assert re_registered.epoch == 2
    assert len(re_registered.epoch_history) == 1
    summary = re_registered.epoch_history[0]
    assert summary.epoch == 1
    assert summary.jobs_claimed == 1
    assert summary.jobs_completed == 1
    assert summary.reason_ended == "manual_reregister"
    assert summary.ended_at is not None
    # New epoch counters are reset.
    assert re_registered.jobs_claimed == 0
    assert re_registered.jobs_completed == 0


@pytest.mark.asyncio
async def test_re_register_after_offline_uses_post_offline_reason(
    fake_clock,
    dispatcher: Dispatcher,
) -> None:
    agent = await dispatcher.register("vg-01", ["vehicle_gateway"])
    # Force the agent offline through the watchdog path.
    fake_clock.advance(dispatcher.heartbeat_timeout_s + 1)
    await dispatcher.reap_stale_agents()
    fresh = dispatcher.registry.get(agent.id)
    assert fresh is not None
    assert fresh.status == AgentStatus.OFFLINE

    # Now re-register; the captured summary should use post_offline reason.
    re_registered = await dispatcher.register("vg-01", ["vehicle_gateway"])
    assert len(re_registered.epoch_history) == 1
    assert re_registered.epoch_history[0].reason_ended == "post_offline"
