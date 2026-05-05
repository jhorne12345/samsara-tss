"""End-to-end happy-path integration test through the HTTP layer."""

from __future__ import annotations

import pytest

from tss.server.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_register_submit_claim_complete_flow(http_client_factory, fake_clock):
    d = Dispatcher()
    async for c in http_client_factory(d):
        # Register
        r = await c.post(
            "/api/agents/register",
            json={"name": "vg-01", "capabilities": ["vehicle_gateway"]},
        )
        assert r.status_code == 200
        agent = r.json()
        agent_id = agent["agent_id"]
        epoch = agent["epoch"]

        # Submit
        r = await c.post(
            "/api/jobs",
            json={"product": "vehicle_gateway", "duration_seconds": 1.0},
        )
        assert r.status_code == 201
        job_id = r.json()["job_id"]

        # Heartbeat (sanity)
        r = await c.post(f"/api/agents/{agent_id}/heartbeat", json={"epoch": epoch})
        assert r.status_code == 204

        # Claim
        r = await c.get(f"/api/agents/{agent_id}/jobs/next")
        assert r.status_code == 200
        assn = r.json()
        assert assn["job_id"] == job_id

        # Result
        r = await c.post(
            f"/api/agents/{agent_id}/jobs/{job_id}/result",
            json={
                "epoch": assn["epoch"],
                "exit_code": 0,
                "duration_actual": 1.0,
            },
        )
        assert r.status_code == 204

        # Fleet snapshot
        r = await c.get("/api/fleet/status")
        assert r.status_code == 200
        s = r.json()["stats"]
        assert s["jobs_completed"] == 1
        assert s["idle"] == 1
        assert s["busy"] == 0
        break  # only need one iteration of the async generator


@pytest.mark.asyncio
async def test_no_job_returns_204(http_client_factory, fake_clock):
    d = Dispatcher()
    async for c in http_client_factory(d):
        r = await c.post(
            "/api/agents/register",
            json={"name": "vg-01", "capabilities": ["vehicle_gateway"]},
        )
        agent_id = r.json()["agent_id"]
        r = await c.get(f"/api/agents/{agent_id}/jobs/next")
        assert r.status_code == 204
        break


@pytest.mark.asyncio
async def test_dashboard_serves_html(http_client_factory, fake_clock):
    d = Dispatcher()
    async for c in http_client_factory(d):
        r = await c.get("/")
        assert r.status_code == 200
        assert b"<html" in r.content.lower()
        assert b"samsara" in r.content.lower()
        break
