"""Integration tests for the `submitter` field — submission, persistence, and filtering."""

from __future__ import annotations

import pytest
from tss.server.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_submit_job_records_submitter(dispatcher: Dispatcher) -> None:
    job = await dispatcher.submit_job(
        product="vehicle_gateway",
        duration_seconds=8.0,
        submitter="alice",
    )
    fetched = dispatcher.store.get(job.id)
    assert fetched is not None
    assert fetched.submitter == "alice"


@pytest.mark.asyncio
async def test_submitter_query_filters_results(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    await dispatcher.submit_job(product="vehicle_gateway", duration_seconds=2.0, submitter="alice")
    await dispatcher.submit_job(product="vehicle_gateway", duration_seconds=2.0, submitter="alice")
    await dispatcher.submit_job(product="asset_gateway", duration_seconds=2.0, submitter="bob")

    async for client in http_client_factory(dispatcher):
        resp = await client.get("/api/jobs", params={"submitter": "alice"})
        assert resp.status_code == 200
        bodies = resp.json()
        assert len(bodies) == 2
        assert all(j["submitter"] == "alice" for j in bodies)


@pytest.mark.asyncio
async def test_submitter_combines_with_other_filters(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    await dispatcher.submit_job(product="vehicle_gateway", duration_seconds=2.0, submitter="alice")
    await dispatcher.submit_job(product="asset_gateway", duration_seconds=2.0, submitter="alice")
    async for client in http_client_factory(dispatcher):
        resp = await client.get(
            "/api/jobs",
            params={"submitter": "alice", "product": "vehicle_gateway"},
        )
        assert resp.status_code == 200
        bodies = resp.json()
        assert len(bodies) == 1
        assert bodies[0]["product"] == "vehicle_gateway"
