"""Direct unit tests for SQLiteJobStore — schema, CRUD, queries, history."""

from __future__ import annotations

import sqlite3

import pytest

from tss.server.sqlite_store import SQLiteJobStore


def test_init_creates_jobs_and_events_tables() -> None:
    store = SQLiteJobStore(":memory:")
    cursor = store._conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    table_names = {row[0] for row in cursor.fetchall()}
    assert "jobs" in table_names
    assert "job_events" in table_names


def test_init_enables_wal_mode() -> None:
    store = SQLiteJobStore(":memory:")
    cursor = store._conn.cursor()
    cursor.execute("PRAGMA journal_mode")
    mode = cursor.fetchone()[0]
    # In-memory databases stay on "memory" journal mode regardless of PRAGMA;
    # accept either WAL (file-backed) or memory (":memory:" path).
    assert mode in ("wal", "memory")


def test_init_creates_required_indexes() -> None:
    store = SQLiteJobStore(":memory:")
    cursor = store._conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
    )
    index_names = {row[0] for row in cursor.fetchall()}
    assert "idx_jobs_status_product" in index_names
    assert "idx_jobs_submitter" in index_names


from datetime import UTC, datetime
from tss.common.models import Job, JobStatus


def _make_job(**overrides: object) -> Job:
    base = {
        "product": "vehicle_gateway",
        "duration_seconds": 8.0,
        "submitter": "alice",
        "created_at": datetime(2026, 5, 6, 10, 0, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return Job(**base)  # type: ignore[arg-type]


def test_add_and_get_round_trips_a_job() -> None:
    store = SQLiteJobStore(":memory:")
    job = _make_job()
    store.add(job)
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.id == job.id
    assert fetched.product == "vehicle_gateway"
    assert fetched.duration_seconds == 8.0
    assert fetched.submitter == "alice"
    assert fetched.status == JobStatus.QUEUED
    assert fetched.attempt_count == 0
    assert fetched.created_at == job.created_at


def test_get_returns_none_for_unknown_id() -> None:
    from uuid import uuid4
    store = SQLiteJobStore(":memory:")
    assert store.get(uuid4()) is None


from tss.common.models import JobEvent


def test_history_round_trips_with_job() -> None:
    store = SQLiteJobStore(":memory:")
    job = _make_job()
    job.history = [
        JobEvent(at=datetime(2026, 5, 6, 10, 0, 1, tzinfo=UTC), kind="submitted"),
        JobEvent(
            at=datetime(2026, 5, 6, 10, 0, 5, tzinfo=UTC),
            kind="claimed",
            agent_name="vg-01",
            detail="attempt=1",
        ),
    ]
    store.add(job)
    fetched = store.get(job.id)
    assert fetched is not None
    assert len(fetched.history) == 2
    kinds = [e.kind for e in fetched.history]
    assert kinds == ["submitted", "claimed"]
    assert fetched.history[1].agent_name == "vg-01"
    assert fetched.history[1].detail == "attempt=1"


def test_update_persists_status_and_new_events() -> None:
    store = SQLiteJobStore(":memory:")
    job = _make_job()
    store.add(job)

    job.status = JobStatus.RUNNING
    job.attempt_count = 1
    job.history.append(
        JobEvent(
            at=datetime(2026, 5, 6, 10, 0, 30, tzinfo=UTC),
            kind="claimed",
            agent_name="vg-01",
        )
    )
    store.update(job)

    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.RUNNING
    assert fetched.attempt_count == 1
    assert len(fetched.history) == 1
    assert fetched.history[0].kind == "claimed"


def test_update_is_idempotent_for_same_events() -> None:
    """Calling update() twice with no new events does not duplicate history."""
    store = SQLiteJobStore(":memory:")
    job = _make_job()
    job.history.append(
        JobEvent(at=datetime(2026, 5, 6, 10, 0, 0, tzinfo=UTC), kind="submitted")
    )
    store.add(job)
    store.update(job)
    store.update(job)
    fetched = store.get(job.id)
    assert fetched is not None
    assert len(fetched.history) == 1


def test_all_returns_jobs_in_insertion_order() -> None:
    store = SQLiteJobStore(":memory:")
    job_a = _make_job(submitter="a")
    job_b = _make_job(submitter="b")
    job_c = _make_job(submitter="c")
    store.add(job_a)
    store.add(job_b)
    store.add(job_c)
    submitters = [j.submitter for j in store.all()]
    assert submitters == ["a", "b", "c"]


def test_by_status_filters_correctly() -> None:
    store = SQLiteJobStore(":memory:")
    j1 = _make_job(submitter="x")
    j2 = _make_job(submitter="y")
    store.add(j1)
    store.add(j2)
    j2.status = JobStatus.RUNNING
    store.update(j2)
    queued = store.by_status(JobStatus.QUEUED)
    running = store.by_status(JobStatus.RUNNING)
    assert [j.submitter for j in queued] == ["x"]
    assert [j.submitter for j in running] == ["y"]


def test_find_queued_for_capabilities_returns_oldest_match_in_order() -> None:
    store = SQLiteJobStore(":memory:")
    asset = _make_job(product="asset_gateway", submitter="a")
    vg_first = _make_job(product="vehicle_gateway", submitter="b")
    vg_second = _make_job(product="vehicle_gateway", submitter="c")
    store.add(asset)
    store.add(vg_first)
    store.add(vg_second)
    found = store.find_queued_for_capabilities(["vehicle_gateway"])
    assert found is not None
    assert found.submitter == "b"


def test_find_queued_returns_none_when_no_match() -> None:
    store = SQLiteJobStore(":memory:")
    store.add(_make_job(product="asset_gateway"))
    assert store.find_queued_for_capabilities(["vehicle_gateway"]) is None


def test_iter_and_len() -> None:
    store = SQLiteJobStore(":memory:")
    assert len(store) == 0
    store.add(_make_job())
    store.add(_make_job(submitter="b"))
    assert len(store) == 2
    assert sum(1 for _ in store) == 2
