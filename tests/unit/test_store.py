"""JobStore Protocol + InMemoryJobStore unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

from tss.common.models import Job, JobStatus
from tss.server.store import InMemoryJobStore


def _make_job(product: str = "vehicle_gateway", submitter: str = "test") -> Job:
    return Job(
        product=product,
        duration_seconds=5.0,
        submitter=submitter,
        created_at=datetime.now(UTC),
    )


def test_inmemory_update_is_a_noop_but_completes() -> None:
    store = InMemoryJobStore()
    job = _make_job()
    store.add(job)
    job.status = JobStatus.RUNNING
    # update() exists on the Protocol; on the in-memory store it is a no-op
    # because Python references already reflect the mutation.
    store.update(job)
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.RUNNING
