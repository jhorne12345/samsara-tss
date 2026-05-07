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
  branch               TEXT,
  commit_sha           TEXT,
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
        # Backwards-compat: older DBs predate `branch` / `commit_sha`. ALTER
        # TABLE ADD COLUMN is cheap and idempotent if we swallow the
        # "duplicate column" error.
        for col_def in ("branch TEXT", "commit_sha TEXT"):
            try:
                self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass
        # Track next insertion_order; recover from existing rows on reopen.
        cursor = self._conn.execute(
            "SELECT COALESCE(MAX(insertion_order), 0) FROM jobs"
        )
        self._next_order: int = int(cursor.fetchone()[0]) + 1

    def close(self) -> None:
        self._conn.close()

    # ----- Serialization helpers -----

    @staticmethod
    def _iso(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return dt.isoformat()

    @staticmethod
    def _parse_iso(s: str | None) -> datetime | None:
        if s is None:
            return None
        return datetime.fromisoformat(s)

    def _row_to_job(self, row: sqlite3.Row, events: list[JobEvent]) -> Job:
        return Job(
            id=UUID(row["id"]),
            product=row["product"],
            status=JobStatus(row["status"]),
            duration_seconds=row["duration_seconds"],
            expected_exit_code=row["expected_exit_code"],
            crash_at_pct=row["crash_at_pct"],
            slow_multiplier=row["slow_multiplier"],
            assigned_agent_id=UUID(row["assigned_agent_id"]) if row["assigned_agent_id"] else None,
            assigned_agent_epoch=row["assigned_agent_epoch"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            submitter=row["submitter"],
            branch=row["branch"] if "branch" in row.keys() else None,
            commit=row["commit_sha"] if "commit_sha" in row.keys() else None,
            created_at=self._parse_iso(row["created_at"]),
            started_at=self._parse_iso(row["started_at"]),
            completed_at=self._parse_iso(row["completed_at"]),
            history=events,
        )

    # ----- Mutations -----

    def add(self, job: Job) -> None:
        order = self._next_order
        self._next_order += 1
        with self._conn:
            self._conn.execute(
                """INSERT INTO jobs (
                  id, product, status, duration_seconds, expected_exit_code,
                  crash_at_pct, slow_multiplier, assigned_agent_id,
                  assigned_agent_epoch, attempt_count, max_attempts,
                  submitter, branch, commit_sha,
                  created_at, started_at, completed_at, insertion_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(job.id), job.product, job.status.value,
                    job.duration_seconds, job.expected_exit_code,
                    job.crash_at_pct, job.slow_multiplier,
                    str(job.assigned_agent_id) if job.assigned_agent_id else None,
                    job.assigned_agent_epoch, job.attempt_count, job.max_attempts,
                    job.submitter, job.branch, job.commit,
                    self._iso(job.created_at), self._iso(job.started_at), self._iso(job.completed_at),
                    order,
                ),
            )
            self._insert_events(job)

    def _insert_events(self, job: Job) -> None:
        if not job.history:
            return
        rows = [
            (
                str(job.id),
                self._iso(e.at),
                e.kind,
                str(e.agent_id) if e.agent_id else None,
                e.agent_name,
                e.detail,
            )
            for e in job.history
        ]
        self._conn.executemany(
            """INSERT OR IGNORE INTO job_events
               (job_id, at, kind, agent_id, agent_name, detail)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )

    def update(self, job: Job) -> None:
        with self._conn:
            self._conn.execute(
                """UPDATE jobs SET
                   product = ?, status = ?, duration_seconds = ?,
                   expected_exit_code = ?, crash_at_pct = ?, slow_multiplier = ?,
                   assigned_agent_id = ?, assigned_agent_epoch = ?,
                   attempt_count = ?, max_attempts = ?, submitter = ?,
                   branch = ?, commit_sha = ?,
                   started_at = ?, completed_at = ?
                   WHERE id = ?""",
                (
                    job.product, job.status.value, job.duration_seconds,
                    job.expected_exit_code, job.crash_at_pct, job.slow_multiplier,
                    str(job.assigned_agent_id) if job.assigned_agent_id else None,
                    job.assigned_agent_epoch, job.attempt_count, job.max_attempts,
                    job.submitter, job.branch, job.commit,
                    self._iso(job.started_at), self._iso(job.completed_at),
                    str(job.id),
                ),
            )
            self._insert_events(job)

    # ----- Reads -----

    def get(self, job_id: UUID) -> Job | None:
        cursor = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),))
        row = cursor.fetchone()
        if row is None:
            return None
        events = self._fetch_events(job_id)
        return self._row_to_job(row, events=events)

    def _fetch_events(self, job_id: UUID) -> list[JobEvent]:
        cursor = self._conn.execute(
            """SELECT at, kind, agent_id, agent_name, detail
               FROM job_events WHERE job_id = ? ORDER BY at ASC""",
            (str(job_id),),
        )
        events: list[JobEvent] = []
        for row in cursor.fetchall():
            events.append(
                JobEvent(
                    at=self._parse_iso(row["at"]),
                    kind=row["kind"],
                    agent_id=UUID(row["agent_id"]) if row["agent_id"] else None,
                    agent_name=row["agent_name"],
                    detail=row["detail"],
                )
            )
        return events

    # ----- Reads (continued) -----

    def all(self) -> list[Job]:
        cursor = self._conn.execute(
            "SELECT * FROM jobs ORDER BY insertion_order ASC"
        )
        return [self._row_to_job(row, self._fetch_events(UUID(row["id"]))) for row in cursor.fetchall()]

    def by_status(self, status: JobStatus) -> list[Job]:
        cursor = self._conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY insertion_order ASC",
            (status.value,),
        )
        return [self._row_to_job(row, self._fetch_events(UUID(row["id"]))) for row in cursor.fetchall()]

    def find_queued_for_capabilities(self, capabilities: Iterable[str]) -> Job | None:
        caps = list(capabilities)
        if not caps:
            return None
        placeholders = ",".join("?" for _ in caps)
        query = (
            f"SELECT * FROM jobs WHERE status = ? AND product IN ({placeholders}) "
            "ORDER BY insertion_order ASC LIMIT 1"
        )
        cursor = self._conn.execute(query, (JobStatus.QUEUED.value, *caps))
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_job(row, self._fetch_events(UUID(row["id"])))

    def __iter__(self) -> Iterator[Job]:
        return iter(self.all())

    def __len__(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM jobs")
        return int(cursor.fetchone()[0])
