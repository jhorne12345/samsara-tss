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
