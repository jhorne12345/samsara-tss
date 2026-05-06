"""Integration test for GET /api/jobs/{job_id}."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tss.server.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_get_job_by_id_returns_full_record(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    job = await dispatcher.submit_job(
        product="vehicle_gateway", duration_seconds=8.0, submitter="alice",
    )
    async for client in http_client_factory(dispatcher):
        resp = await client.get(f"/api/jobs/{job.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(job.id)
        assert body["submitter"] == "alice"
        assert body["product"] == "vehicle_gateway"
        assert len(body["history"]) >= 1
        assert body["history"][0]["kind"] == "submitted"


@pytest.mark.asyncio
async def test_get_job_by_id_returns_404_for_unknown(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    async for client in http_client_factory(dispatcher):
        resp = await client.get(f"/api/jobs/{uuid4()}")
        assert resp.status_code == 404
