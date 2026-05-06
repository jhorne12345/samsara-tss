"""Integration test for the /metrics endpoint."""

from __future__ import annotations

import pytest

from tss.server.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_text(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    await dispatcher.register(name="vg-01", capabilities=["vehicle_gateway"])
    await dispatcher.submit_job(
        product="vehicle_gateway", duration_seconds=8.0, submitter="alice",
    )
    async for client in http_client_factory(dispatcher):
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        body = resp.text
        assert "tss_jobs_queued" in body
        assert "tss_jobs_running" in body
        assert "tss_agents_total" in body
        assert "# HELP tss_jobs_queued" in body
        assert "# TYPE tss_jobs_queued gauge" in body
