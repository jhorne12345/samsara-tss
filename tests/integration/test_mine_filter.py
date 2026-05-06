"""Tests that lock in the contract the dashboard's "Mine only" filter
depends on.

The toggle itself is client-side, but it can only work if:

1. /api/fleet/status events include a ``submitter`` field per event.
2. /api/jobs?submitter=X returns only that user's jobs.

These tests guard both.
"""

from __future__ import annotations

import pytest

from tss.server.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_fleet_status_events_include_submitter(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    a = await dispatcher.register("vg-01", ["vehicle_gateway"])
    await dispatcher.submit_job(
        product="vehicle_gateway", duration_seconds=8.0, submitter="Alice",
    )
    await dispatcher.claim_next_job(a.id)

    async for client in http_client_factory(dispatcher):
        resp = await client.get("/api/fleet/status")
        assert resp.status_code == 200
        events = resp.json()["recent_events"]
        assert events, "expected at least one event"
        # Every event flowing through the dashboard feed must carry the
        # submitter so the client-side Mine-only filter can match.
        for e in events:
            assert "submitter" in e, f"event missing submitter: {e}"
            assert e["submitter"] == "Alice"


@pytest.mark.asyncio
async def test_fleet_status_includes_recent_completed(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    a = await dispatcher.register("vg-01", ["vehicle_gateway"])
    job = await dispatcher.submit_job(
        product="vehicle_gateway", duration_seconds=1.0, submitter="alice",
    )
    await dispatcher.claim_next_job(a.id)
    await dispatcher.report_result(
        agent_id=a.id, job_id=job.id, epoch=a.epoch,
        exit_code=0, duration_actual=1.0,
    )

    async for client in http_client_factory(dispatcher):
        resp = await client.get("/api/fleet/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "recent_completed" in body
        ids = [j["id"] for j in body["recent_completed"]]
        assert str(job.id) in ids


@pytest.mark.asyncio
async def test_jobs_filter_by_submitter(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    await dispatcher.submit_job(
        product="vehicle_gateway", duration_seconds=8.0, submitter="alice",
    )
    await dispatcher.submit_job(
        product="vehicle_gateway", duration_seconds=8.0, submitter="bob",
    )

    async for client in http_client_factory(dispatcher):
        resp = await client.get("/api/jobs", params={"submitter": "alice"})
        assert resp.status_code == 200
        jobs = resp.json()
        assert len(jobs) == 1
        assert jobs[0]["submitter"] == "alice"
