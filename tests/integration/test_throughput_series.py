"""Integration tests for the throughput sparkline series.

The series is a list of jobs-completed-per-minute over the last
``THROUGHPUT_BUCKETS`` minutes (oldest first, current minute last). It feeds
the operator view's sparkline. These tests use the FakeClock fixture so
they don't depend on wall time.
"""

from __future__ import annotations

import pytest

from tss.server.dispatcher import THROUGHPUT_BUCKETS, Dispatcher


@pytest.mark.asyncio
async def test_throughput_series_length_is_fixed(dispatcher: Dispatcher) -> None:
    snap = await dispatcher.snapshot_fleet()
    assert len(snap.stats.throughput_per_min) == THROUGHPUT_BUCKETS


@pytest.mark.asyncio
async def test_throughput_records_completion_in_current_bucket(
    fake_clock,
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
    snap = await dispatcher.snapshot_fleet()
    # The completion landed in the last (current) bucket.
    assert snap.stats.throughput_per_min[-1] == 1
    assert sum(snap.stats.throughput_per_min) == 1


@pytest.mark.asyncio
async def test_throughput_shifts_when_clock_advances(
    fake_clock,
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
    # Advance two minutes; the completion should shift two buckets earlier.
    fake_clock.advance(120.0)
    snap = await dispatcher.snapshot_fleet()
    assert snap.stats.throughput_per_min[-1] == 0
    assert snap.stats.throughput_per_min[-3] == 1


@pytest.mark.asyncio
async def test_throughput_drops_completions_older_than_window(
    fake_clock,
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
    # Advance well past the window; the completion should fall off entirely.
    fake_clock.advance(60.0 * (THROUGHPUT_BUCKETS + 2))
    snap = await dispatcher.snapshot_fleet()
    assert sum(snap.stats.throughput_per_min) == 0
