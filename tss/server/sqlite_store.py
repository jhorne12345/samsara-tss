"""SQLite-backed JobStore.

Synchronous (stdlib `sqlite3`) because the JobStore Protocol is sync and
SQLite operations are sub-millisecond local I/O — `aiosqlite` would force
the Protocol async and cascade through the dispatcher for no real-world
benefit. All access from the dispatcher is already serialized by the
dispatcher's asyncio.Lock; the DB just durably stores serialized state.

Single connection per store instance. WAL mode for file-backed DBs so
readers don't block writers. ":memory:" path supported for tests.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from datetime import datetime
from uuid import UUID

from tss.common.models import Job, JobEvent, JobStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id                   TEXT    PRIMARY KEY,
  product              TEXT    NOT NULL,
  status               TEXT    NOT NULL,
  duration_seconds     REAL    NOT NULL,
  expected_exit_code   INTEGER NOT NULL,
  crash_at_pct         REAL,
  slow_multiplier      REAL    NOT NULL DEFAULT 1.0,
  assigned_agent_id    TEXT,
  assigned_agent_epoch INTEGER,
  attempt_count        INTEGER NOT NULL DEFAULT 0,
  max_attempts         INTEGER NOT NULL DEFAULT 3,
  submitter            TEXT    NOT NULL,
  created_at           TEXT    NOT NULL,
  started_at           TEXT,
  completed_at         TEXT,
  insertion_order      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_product ON jobs(status, product);
CREATE INDEX IF NOT EXISTS idx_jobs_submitter      ON jobs(submitter);

CREATE TABLE IF NOT EXISTS job_events (
  job_id     TEXT NOT NULL,
  at         TEXT NOT NULL,
  kind       TEXT NOT NULL,
  agent_id   TEXT,
  agent_name TEXT,
  detail     TEXT,
  PRIMARY KEY (job_id, at, kind),
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
"""


class SQLiteJobStore:
    """Implements the JobStore Protocol against a SQLite database.

    Insertion order is recorded explicitly in the `insertion_order` column so
    that ``find_queued_for_capabilities`` returns jobs in submission order —
    matching the in-memory dict semantics the dispatcher was designed against.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(
            db_path,
            isolation_level=None,  # autocommit; we batch via explicit transactions
            check_same_thread=False,  # we serialize via the dispatcher's lock
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # WAL mode: file-backed DBs benefit; ":memory:" stays on memory journaling.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)
        # Track next insertion_order; recover from existing rows on reopen.
        cursor = self._conn.execute(
            "SELECT COALESCE(MAX(insertion_order), 0) FROM jobs"
        )
        self._next_order: int = int(cursor.fetchone()[0]) + 1

    def close(self) -> None:
        self._conn.close()
