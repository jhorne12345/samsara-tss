"""Integration test for GET /api/agents/{agent_id}/history."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tss.server.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_agent_history_returns_events_filtered_to_agent(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    a = await dispatcher.register("vg-01", ["vehicle_gateway"])
    b = await dispatcher.register("vg-02", ["vehicle_gateway"])
    job = await dispatcher.submit_job(
        product="vehicle_gateway", duration_seconds=8.0, submitter="alice",
    )
    # Agent A claims the job; that records a 'claimed' event with agent_id=a.
    assignment = await dispatcher.claim_next_job(a.id)
    assert assignment is not None and assignment.job_id == job.id

    async for client in http_client_factory(dispatcher):
        resp = await client.get(f"/api/agents/{a.id}/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["agent"]["id"] == str(a.id)
        kinds = [e["kind"] for e in body["events"]]
        assert "claimed" in kinds
        assert all(e["job_id"] == str(job.id) for e in body["events"])

        # B has no events yet; history is empty but the agent record is there.
        resp_b = await client.get(f"/api/agents/{b.id}/history")
        assert resp_b.status_code == 200
        assert resp_b.json()["events"] == []


@pytest.mark.asyncio
async def test_agent_history_returns_404_for_unknown(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    async for client in http_client_factory(dispatcher):
        resp = await client.get(f"/api/agents/{uuid4()}/history")
        assert resp.status_code == 404
