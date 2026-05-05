"""Capability-matching integration: 3 agents, 3 jobs, verify routing."""

from __future__ import annotations

import pytest

from tss.server.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_three_agents_three_jobs_route_correctly(http_client_factory, fake_clock):
    d = Dispatcher()
    async for c in http_client_factory(d):
        # Register one VG-only, one AG-only, one combo
        agents = {}
        for name, caps in [
            ("vg-only", ["vehicle_gateway"]),
            ("ag-only", ["asset_gateway"]),
            ("combo", ["vehicle_gateway", "asset_gateway"]),
        ]:
            r = await c.post(
                "/api/agents/register",
                json={"name": name, "capabilities": caps},
            )
            agents[name] = r.json()

        # Submit 1 VG job and 2 AG jobs
        for product in ["vehicle_gateway", "asset_gateway", "asset_gateway"]:
            r = await c.post(
                "/api/jobs",
                json={"product": product, "duration_seconds": 1.0},
            )
            assert r.status_code == 201

        # vg-only and ag-only and combo all poll once
        results = {}
        for name in ("vg-only", "ag-only", "combo"):
            r = await c.get(f"/api/agents/{agents[name]['agent_id']}/jobs/next")
            results[name] = r.json() if r.status_code == 200 else None

        # vg-only must have got a vehicle_gateway job
        assert results["vg-only"] is not None
        assert results["vg-only"]["product"] == "vehicle_gateway"

        # ag-only must have got an asset_gateway job
        assert results["ag-only"] is not None
        assert results["ag-only"]["product"] == "asset_gateway"

        # combo must have got the remaining asset_gateway job
        assert results["combo"] is not None
        assert results["combo"]["product"] == "asset_gateway"

        # All jobs are now running, queue should be empty
        r = await c.get("/api/fleet/status")
        s = r.json()["stats"]
        assert s["queue_depth"] == 0
        assert s["jobs_running"] == 3
        break


@pytest.mark.asyncio
async def test_vg_agent_does_not_claim_ag_job(http_client_factory, fake_clock):
    d = Dispatcher()
    async for c in http_client_factory(d):
        r = await c.post(
            "/api/agents/register",
            json={"name": "vg-01", "capabilities": ["vehicle_gateway"]},
        )
        agent_id = r.json()["agent_id"]
        await c.post("/api/jobs", json={"product": "asset_gateway", "duration_seconds": 1.0})
        r = await c.get(f"/api/agents/{agent_id}/jobs/next")
        assert r.status_code == 204
        break
