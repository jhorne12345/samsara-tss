"""Verifies the dispatcher calls store.update(job) after every Job mutation.

This is the contract SQLiteJobStore relies on to persist in-place mutations
the dispatcher does on Pydantic models.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from tss.common.models import Job
from tss.server.dispatcher import Dispatcher
from tss.server.registry import InMemoryAgentRegistry
from tss.server.store import InMemoryJobStore


class CountingStore(InMemoryJobStore):
    def __init__(self) -> None:
        super().__init__()
        self.update_calls: list[UUID] = []

    def update(self, job: Job) -> None:
        super().update(job)
        self.update_calls.append(job.id)


@pytest.mark.asyncio
async def test_claim_calls_update() -> None:
    store = CountingStore()
    registry = InMemoryAgentRegistry()
    d = Dispatcher(registry=registry, store=store)

    agent = await d.register(name="vg-01", capabilities=["vehicle_gateway"])
    job = await d.submit_job(
        product="vehicle_gateway", duration_seconds=2.0, submitter="t",
    )
    store.update_calls.clear()  # ignore submission's own update (if any)

    assignment = await d.claim_next_job(agent.id)
    assert assignment is not None
    assert job.id in store.update_calls, (
        "claim_next_job must call store.update(job) so SQLite-backed stores "
        "persist the RUNNING transition"
    )


@pytest.mark.asyncio
async def test_report_result_calls_update() -> None:
    store = CountingStore()
    registry = InMemoryAgentRegistry()
    d = Dispatcher(registry=registry, store=store)

    agent = await d.register(name="vg-01", capabilities=["vehicle_gateway"])
    job = await d.submit_job(product="vehicle_gateway", duration_seconds=2.0, submitter="t")
    assignment = await d.claim_next_job(agent.id)
    assert assignment is not None
    store.update_calls.clear()

    await d.report_result(
        agent_id=agent.id, job_id=job.id, epoch=assignment.epoch,
        exit_code=0, duration_actual=2.0,
    )
    assert job.id in store.update_calls


@pytest.mark.asyncio
async def test_offline_reassignment_calls_update(fake_clock: Any) -> None:
    store = CountingStore()
    registry = InMemoryAgentRegistry()
    d = Dispatcher(registry=registry, store=store, heartbeat_timeout_s=1.0)

    agent = await d.register(name="vg-01", capabilities=["vehicle_gateway"])
    job = await d.submit_job(product="vehicle_gateway", duration_seconds=2.0, submitter="t")
    await d.claim_next_job(agent.id)
    store.update_calls.clear()

    fake_clock.advance(5.0)
    await d.reap_stale_agents()
    assert job.id in store.update_calls
