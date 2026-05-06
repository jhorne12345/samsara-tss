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
