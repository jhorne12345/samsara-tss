"""Integration tests for the optional `branch` and `commit` fields on Job.

These power the engineer-view "branch · commit" line in the My Build hero.
The tests guard the contract end-to-end: payload accepted, persisted to the
job store, returned by the GET endpoints.
"""

from __future__ import annotations

import pytest

from tss.server.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_submit_job_persists_branch_and_commit(dispatcher: Dispatcher) -> None:
    job = await dispatcher.submit_job(
        product="vehicle_gateway",
        duration_seconds=4.0,
        submitter="alice",
        branch="al/can-bus-init",
        commit="4e9f1c7",
    )
    fetched = dispatcher.store.get(job.id)
    assert fetched is not None
    assert fetched.branch == "al/can-bus-init"
    assert fetched.commit == "4e9f1c7"


@pytest.mark.asyncio
async def test_submit_job_branch_commit_are_optional(dispatcher: Dispatcher) -> None:
    job = await dispatcher.submit_job(
        product="vehicle_gateway",
        duration_seconds=4.0,
        submitter="alice",
    )
    fetched = dispatcher.store.get(job.id)
    assert fetched is not None
    assert fetched.branch is None
    assert fetched.commit is None


@pytest.mark.asyncio
async def test_post_jobs_accepts_branch_and_commit(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    async for client in http_client_factory(dispatcher):
        resp = await client.post(
            "/api/jobs",
            json={
                "product": "vehicle_gateway",
                "duration_seconds": 4.0,
                "submitter": "alice",
                "branch": "al/can-bus-init",
                "commit": "4e9f1c7",
            },
        )
        assert resp.status_code == 201
        job_id = resp.json()["job_id"]

        detail = await client.get(f"/api/jobs/{job_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["branch"] == "al/can-bus-init"
        assert body["commit"] == "4e9f1c7"
