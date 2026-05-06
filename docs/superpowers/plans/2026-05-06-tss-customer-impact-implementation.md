# TSS Customer-Impact Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-05-06-tss-customer-impact-design.md`

**Goal:** Add SQLite-backed job persistence + a `submitter` field + customer-facing dashboard extensions (identity prompt, *Mine* filter, job detail panel, completion toast) and a `/metrics` endpoint, without rewriting the dashboard in a UI framework.

**Architecture:** Existing `JobStore` Protocol gains an explicit `update(job)` method so SQLite can persist in-place mutations. Stdlib `sqlite3` (sync) is used because the Protocol is sync and SQLite ops are sub-millisecond local I/O — no new dependencies. The dispatcher's single `asyncio.Lock` and epoch invariant are unchanged. Customer-facing dashboard additions are surgical edits to the existing hand-rolled HTML/CSS/JS — no framework, no build step.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, stdlib `sqlite3`, vanilla HTML/CSS/JS, browser `Notification` API. Zero new Python or JS dependencies.

**File map (created or modified):**

| File | Status | Responsibility |
|---|---|---|
| `tss/common/models.py` | modify | Add `submitter: str` field to `Job` and `JobSubmitRequest` |
| `tss/server/store.py` | modify | Add `update(job)` to Protocol; later, delete `InMemoryJobStore` |
| `tss/server/sqlite_store.py` | create | `SQLiteJobStore` — sync stdlib sqlite3 backend |
| `tss/server/dispatcher.py` | modify | Default DI to SQLiteJobStore; thread `submitter` through `submit_job`; call `store.update(job)` after every Job mutation |
| `tss/server/routes/jobs.py` | modify | `?submitter=` query param; new `GET /api/jobs/{id}` endpoint |
| `tss/server/routes/metrics.py` | create | Hand-rolled Prometheus text format endpoint |
| `tss/server/app.py` | modify | Register metrics router |
| `tss/cli.py` | modify | `--submitter` and `--db-path` flags on `serve` and `submit-job` |
| `tests/conftest.py` | modify | New fixtures providing `SQLiteJobStore(":memory:")` |
| `tests/unit/test_sqlite_store.py` | create | Direct CRUD tests against the new store |
| `tests/integration/test_submitter_filter.py` | create | API integration test for `?submitter=` |
| `tests/integration/test_job_detail.py` | create | API integration test for `GET /api/jobs/{id}` |
| `tests/integration/test_metrics.py` | create | API integration test for `/metrics` |
| `tss/server/static/index.html` | modify | Identity banner, *Mine* toggle, detail panel HTML |
| `tss/server/static/style.css` | modify | Styles for new elements (slide-in panel, banner, toggle) |
| `tss/server/static/app.js` | modify | Identity persistence, filter logic, panel renderer, completion notifications |

---

## Task 1: Add `submitter` field and thread it through CLI → API → Dispatcher

**Files:**
- Modify: `tss/common/models.py` (add `submitter` to `Job` and `JobSubmitRequest`)
- Modify: `tss/server/dispatcher.py` (`submit_job` method signature + body)
- Modify: `tss/server/routes/jobs.py` (`submit_job` route handler passes through)
- Modify: `tss/cli.py` (`--submitter` flag, `os.environ["USER"]` default)
- Test: `tests/integration/test_submitter_filter.py` (new file — first test exercises submission carries submitter)

- [ ] **Step 1: Write the failing test (submission carries submitter)**

Create `tests/integration/test_submitter_filter.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

```
cd /Users/jackson/dev/Samsara && .venv/bin/pytest tests/integration/test_submitter_filter.py::test_submit_job_records_submitter -v
```

Expected: FAIL with `TypeError: submit_job() got an unexpected keyword argument 'submitter'` (or similar).

- [ ] **Step 3: Add `submitter` to `Job` and `JobSubmitRequest` in `tss/common/models.py`**

In `class Job`, after `slow_multiplier`:

```python
    submitter: str
    """Who submitted this job. Honor system; populated by CLI ($USER) or web ui (localStorage)."""
```

In `class JobSubmitRequest`, after `slow_multiplier`:

```python
    submitter: str = "unknown"
    """Free-form identifier. Set by CLI (defaults to $USER) or web (localStorage)."""
```

- [ ] **Step 4: Update `Dispatcher.submit_job` in `tss/server/dispatcher.py`**

Find the `submit_job` method. Update its signature and body:

```python
    async def submit_job(
        self,
        *,
        product: str,
        duration_seconds: float,
        expected_exit_code: int = 0,
        crash_at_pct: float | None = None,
        slow_multiplier: float = 1.0,
        max_attempts: int | None = None,
        submitter: str = "unknown",
    ) -> Job:
        async with self._lock:
            now = clock.utcnow()
            job = Job(
                product=product,
                duration_seconds=duration_seconds,
                expected_exit_code=expected_exit_code,
                crash_at_pct=crash_at_pct,
                slow_multiplier=slow_multiplier,
                max_attempts=max_attempts or self.default_max_attempts,
                submitter=submitter,
                created_at=now,
                history=[JobEvent(at=now, kind="submitted", detail=f"product={product}")],
            )
            self.store.add(job)
            log.info(
                "job submitted id=%s product=%s duration=%s submitter=%s",
                job.id, product, duration_seconds, submitter,
            )
            return job
```

- [ ] **Step 5: Pass through in `tss/server/routes/jobs.py` `submit_job` handler**

Update the body:

```python
@router.post("/jobs", response_model=JobSubmitResponse, status_code=status.HTTP_201_CREATED)
async def submit_job(req: JobSubmitRequest, request: Request) -> JobSubmitResponse:
    d = _disp(request)
    job = await d.submit_job(
        product=req.product,
        duration_seconds=req.duration_seconds,
        expected_exit_code=req.expected_exit_code,
        crash_at_pct=req.crash_at_pct,
        slow_multiplier=req.slow_multiplier,
        max_attempts=req.max_attempts,
        submitter=req.submitter,
    )
    return JobSubmitResponse(job_id=job.id)
```

- [ ] **Step 6: Update CLI `submit-job` in `tss/cli.py` to accept `--submitter` defaulting to `$USER`**

Find the `submit_job` CLI command. Add the option and pass it through. The exact form depends on Typer's existing pattern; the change is roughly:

```python
@app.command("submit-job")
def submit_job_cmd(
    product: str = typer.Option(..., "--product"),
    duration: float = typer.Option(..., "--duration"),
    crash_at: float | None = typer.Option(None, "--crash-at"),
    slow_multiplier: float = typer.Option(1.0, "--slow-multiplier"),
    max_attempts: int = typer.Option(3, "--max-attempts"),
    expected_exit: int = typer.Option(0, "--expected-exit"),
    submitter: str = typer.Option(
        default_factory=lambda: os.environ.get("USER", "unknown"),
        help="Identifier of the submitter. Defaults to $USER.",
    ),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8080, "--port"),
) -> None:
    ...
    payload = {
        "product": product,
        "duration_seconds": duration,
        "crash_at_pct": crash_at,
        "slow_multiplier": slow_multiplier,
        "max_attempts": max_attempts,
        "expected_exit_code": expected_exit,
        "submitter": submitter,
    }
    ...
```

Match the existing command style in `cli.py` exactly when editing.

- [ ] **Step 7: Run the failing test to verify it now passes**

```
.venv/bin/pytest tests/integration/test_submitter_filter.py::test_submit_job_records_submitter -v
```

Expected: PASS.

- [ ] **Step 8: Run all existing tests to verify no regression**

```
.venv/bin/pytest -m "not chaos" -v
```

Expected: all green. Some existing tests may need to set `submitter` if they construct `Job` directly. If so, fix them.

- [ ] **Step 9: Commit**

```
git add tss/common/models.py tss/server/dispatcher.py tss/server/routes/jobs.py tss/cli.py tests/integration/test_submitter_filter.py
git commit -m "$(cat <<'EOF'
feat: add submitter field to Job + thread through CLI/API/dispatcher

The first half of the customer-impact upgrade: every job now records who
submitted it so the dashboard can filter to "Mine" later. CLI defaults to
$USER; web form will set it from localStorage in a later task.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `update()` to `JobStore` Protocol; implement on `InMemoryJobStore` as a no-op

**Files:**
- Modify: `tss/server/store.py` (Protocol + InMemoryJobStore.update)
- Test: a new test in `tests/unit/test_store.py` (create if missing)

This is preparation for SQLite. We need an explicit write-back call at every mutation site, but for the in-memory store it's a no-op since the dispatcher already mutates references. Adding it now lets us thread it through the dispatcher (Task 3) before the SQLite backend exists.

- [ ] **Step 1: Write the failing test**

Create or extend `tests/unit/test_store.py`:

```python
"""JobStore Protocol + InMemoryJobStore unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

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
```

- [ ] **Step 2: Run to verify it fails**

```
.venv/bin/pytest tests/unit/test_store.py::test_inmemory_update_is_a_noop_but_completes -v
```

Expected: FAIL with `AttributeError: 'InMemoryJobStore' object has no attribute 'update'`.

- [ ] **Step 3: Add `update` to the Protocol and the in-memory class**

In `tss/server/store.py`, update the Protocol:

```python
class JobStore(Protocol):
    def add(self, job: Job) -> None: ...
    def update(self, job: Job) -> None: ...
    def get(self, job_id: UUID) -> Job | None: ...
    def all(self) -> list[Job]: ...
    def by_status(self, status: JobStatus) -> list[Job]: ...
    def find_queued_for_capabilities(self, capabilities: Iterable[str]) -> Job | None: ...
    def __iter__(self) -> Iterator[Job]: ...
    def __len__(self) -> int: ...
```

And the implementation:

```python
    def update(self, job: Job) -> None:
        # In-memory: the Pydantic model the caller mutated is already the
        # object we hold in self._jobs, so no work to do. The method exists
        # to satisfy the Protocol so the SQLiteJobStore can persist the
        # mutation.
        self._jobs[job.id] = job
```

(The reassignment is defensive — handles the case of a caller passing in a model created elsewhere.)

- [ ] **Step 4: Run to verify it passes**

```
.venv/bin/pytest tests/unit/test_store.py::test_inmemory_update_is_a_noop_but_completes -v
```

Expected: PASS.

- [ ] **Step 5: Run all tests to verify no regression**

```
.venv/bin/pytest -m "not chaos" -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```
git add tss/server/store.py tests/unit/test_store.py
git commit -m "$(cat <<'EOF'
refactor: add update(job) to JobStore Protocol

Preparation for SQLite persistence. In-memory store keeps references so
update() is a no-op; with SQLite it will become an UPDATE statement.
Protocol-level addition first, dispatcher wiring next, SQLite backend
after that — each step independently testable.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Wire `store.update(job)` into Dispatcher mutation sites

**Files:**
- Modify: `tss/server/dispatcher.py` (call `self.store.update(job)` after every Job mutation)
- Test: `tests/unit/test_dispatcher_persistence.py` (new file with a counting fake)

We use a counting fake `JobStore` to verify every mutation site calls `update()`. This is the test that will fail before we wire the dispatcher.

- [ ] **Step 1: Write the failing test (counting fake)**

Create `tests/unit/test_dispatcher_persistence.py`:

```python
"""Verifies the dispatcher calls store.update(job) after every Job mutation.

This is the contract SQLiteJobStore relies on to persist in-place mutations
the dispatcher does on Pydantic models.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any
from uuid import UUID

import pytest

from tss.common.models import Agent, AgentStatus, Job, JobStatus
from tss.server.dispatcher import Dispatcher
from tss.server.registry import InMemoryAgentRegistry
from tss.server.store import InMemoryJobStore


class CountingStore(InMemoryJobStore):
    def __init__(self) -> None:
        super().__init__()
        self.update_calls: list[UUID] = []

    def update(self, job: Job) -> None:
        super().update(job)
        self.update_calls.append(job.id)


@pytest.mark.asyncio
async def test_claim_calls_update() -> None:
    store = CountingStore()
    registry = InMemoryAgentRegistry()
    d = Dispatcher(registry=registry, store=store)

    agent = await d.register(name="vg-01", capabilities=["vehicle_gateway"])
    job = await d.submit_job(
        product="vehicle_gateway", duration_seconds=2.0, submitter="t",
    )
    store.update_calls.clear()  # ignore submission's own update (if any)

    assignment = await d.claim_next_job(agent.id)
    assert assignment is not None
    assert job.id in store.update_calls, (
        "claim_next_job must call store.update(job) so SQLite-backed stores "
        "persist the RUNNING transition"
    )


@pytest.mark.asyncio
async def test_report_result_calls_update() -> None:
    store = CountingStore()
    registry = InMemoryAgentRegistry()
    d = Dispatcher(registry=registry, store=store)

    agent = await d.register(name="vg-01", capabilities=["vehicle_gateway"])
    job = await d.submit_job(product="vehicle_gateway", duration_seconds=2.0, submitter="t")
    assignment = await d.claim_next_job(agent.id)
    assert assignment is not None
    store.update_calls.clear()

    await d.report_result(
        agent_id=agent.id, job_id=job.id, epoch=assignment.epoch,
        exit_code=0, duration_actual=2.0,
    )
    assert job.id in store.update_calls


@pytest.mark.asyncio
async def test_offline_reassignment_calls_update(fake_clock: Any) -> None:
    store = CountingStore()
    registry = InMemoryAgentRegistry()
    d = Dispatcher(registry=registry, store=store, heartbeat_timeout_s=1.0)

    agent = await d.register(name="vg-01", capabilities=["vehicle_gateway"])
    job = await d.submit_job(product="vehicle_gateway", duration_seconds=2.0, submitter="t")
    await d.claim_next_job(agent.id)
    store.update_calls.clear()

    fake_clock.advance(5.0)
    await d.reap_stale_agents()
    assert job.id in store.update_calls
```

- [ ] **Step 2: Run to verify it fails**

```
.venv/bin/pytest tests/unit/test_dispatcher_persistence.py -v
```

Expected: at least one of the three tests FAILs with the assertion message about `store.update(job)` not being called.

- [ ] **Step 3: Wire `store.update(job)` into the dispatcher's mutation sites**

In `tss/server/dispatcher.py`:

In `submit_job` after `self.store.add(job)`, also note that `add()` already inserts; no `update()` needed.

In `claim_next_job` — after the entire mutation block (`history.append`, `agent.status = BUSY`, `agent.current_job_id = job.id`):

```python
            self.store.update(job)
            agent.status = AgentStatus.BUSY
            agent.current_job_id = job.id
            ...
            return JobAssignment(...)
```

Specifically, place `self.store.update(job)` *after* all `job.*` mutations, before the agent mutations.

In `report_result` — after each branch that mutates the job (the two cases in the `else` for failed exits, and the COMPLETED case), call `self.store.update(job)` once after the if/else block:

```python
            now = clock.utcnow()
            if exit_code == job.expected_exit_code:
                job.status = JobStatus.COMPLETED
                job.completed_at = now
                job.history.append(JobEvent(...))
                log.info(...)
            else:
                detail = f"exit={exit_code} ..."
                if job.attempt_count >= job.max_attempts:
                    job.status = JobStatus.FAILED
                    job.completed_at = now
                    job.history.append(...)
                else:
                    job.status = JobStatus.QUEUED
                    job.assigned_agent_id = None
                    job.assigned_agent_epoch = None
                    job.started_at = None
                    job.history.append(...)

            self.store.update(job)  # <-- NEW

            if agent.current_job_id == job.id:
                agent.status = AgentStatus.IDLE
                agent.current_job_id = None
```

In `_requeue_job_locked` — at the end of the method, after both branches:

```python
    def _requeue_job_locked(self, job: Job, now: datetime, *, kind: _RequeueKind, ...) -> None:
        job.assigned_agent_id = None
        job.assigned_agent_epoch = None
        job.started_at = None
        if job.attempt_count >= job.max_attempts:
            job.status = JobStatus.FAILED
            job.completed_at = now
            job.history.append(...)
            log.warning(...)
        else:
            job.status = JobStatus.QUEUED
            job.history.append(...)
            log.info(...)
        self.store.update(job)  # <-- NEW
```

- [ ] **Step 4: Run the failing test to verify it now passes**

```
.venv/bin/pytest tests/unit/test_dispatcher_persistence.py -v
```

Expected: all three PASS.

- [ ] **Step 5: Run all existing tests to confirm no regression**

```
.venv/bin/pytest -m "not chaos" -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```
git add tss/server/dispatcher.py tests/unit/test_dispatcher_persistence.py
git commit -m "$(cat <<'EOF'
refactor: dispatcher calls store.update(job) after every Job mutation

Wires the persistence contract introduced in the previous commit. With the
in-memory store these calls are no-ops; with SQLiteJobStore (next commit)
they become real writes. Three new tests use a counting fake JobStore to
prove the contract is upheld at every mutation site (claim, report,
offline reassignment, overrun).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Skeleton `SQLiteJobStore` — connection + schema init

**Files:**
- Create: `tss/server/sqlite_store.py`
- Create: `tests/unit/test_sqlite_store.py`

- [ ] **Step 1: Write the failing test (init creates schema)**

Create `tests/unit/test_sqlite_store.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

```
.venv/bin/pytest tests/unit/test_sqlite_store.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tss.server.sqlite_store'`.

- [ ] **Step 3: Create `tss/server/sqlite_store.py` with schema init**

```python
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

import json
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from uuid import UUID

from tss.common.models import Job, JobEvent, JobEventKind, JobStatus

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
```

- [ ] **Step 4: Run to verify it passes**

```
.venv/bin/pytest tests/unit/test_sqlite_store.py -v
```

Expected: all three tests PASS.

- [ ] **Step 5: Commit**

```
git add tss/server/sqlite_store.py tests/unit/test_sqlite_store.py
git commit -m "$(cat <<'EOF'
feat: SQLiteJobStore skeleton — connection, schema, indexes

Stdlib sqlite3, single connection, WAL mode for file-backed DBs.
insertion_order column preserves the FIFO semantics the dispatcher
was designed against. CRUD, queries, and history persistence in
follow-up commits.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `SQLiteJobStore.add()` and `.get()` — round-trip a Job (no history yet)

**Files:**
- Modify: `tss/server/sqlite_store.py`
- Modify: `tests/unit/test_sqlite_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_sqlite_store.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

```
.venv/bin/pytest tests/unit/test_sqlite_store.py::test_add_and_get_round_trips_a_job -v
```

Expected: FAIL — `add` and `get` not implemented.

- [ ] **Step 3: Implement `add` and `get` in `SQLiteJobStore`**

Append to `tss/server/sqlite_store.py`:

```python
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
            created_at=self._parse_iso(row["created_at"]),  # type: ignore[arg-type]
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
                """
                INSERT INTO jobs (
                  id, product, status, duration_seconds, expected_exit_code,
                  crash_at_pct, slow_multiplier, assigned_agent_id,
                  assigned_agent_epoch, attempt_count, max_attempts,
                  submitter, created_at, started_at, completed_at, insertion_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(job.id),
                    job.product,
                    job.status.value,
                    job.duration_seconds,
                    job.expected_exit_code,
                    job.crash_at_pct,
                    job.slow_multiplier,
                    str(job.assigned_agent_id) if job.assigned_agent_id else None,
                    job.assigned_agent_epoch,
                    job.attempt_count,
                    job.max_attempts,
                    job.submitter,
                    self._iso(job.created_at),
                    self._iso(job.started_at),
                    self._iso(job.completed_at),
                    order,
                ),
            )
            # History is added separately in Task 6.

    # ----- Reads -----

    def get(self, job_id: UUID) -> Job | None:
        cursor = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        # History fetched in Task 6 — for now, empty list.
        return self._row_to_job(row, events=[])
```

- [ ] **Step 4: Run to verify it passes**

```
.venv/bin/pytest tests/unit/test_sqlite_store.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```
git add tss/server/sqlite_store.py tests/unit/test_sqlite_store.py
git commit -m "$(cat <<'EOF'
feat: SQLiteJobStore add() and get() — round-trip a Job

History persistence in the next commit.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `SQLiteJobStore` — JobEvent history persistence

**Files:**
- Modify: `tss/server/sqlite_store.py`
- Modify: `tests/unit/test_sqlite_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_sqlite_store.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

```
.venv/bin/pytest tests/unit/test_sqlite_store.py::test_history_round_trips_with_job -v
```

Expected: FAIL with `assert len(fetched.history) == 2` (history currently empty).

- [ ] **Step 3: Persist and read events**

Update `add()` in `tss/server/sqlite_store.py` to insert events inside the same transaction:

```python
    def add(self, job: Job) -> None:
        order = self._next_order
        self._next_order += 1
        with self._conn:
            self._conn.execute(
                """INSERT INTO jobs (
                  id, product, status, duration_seconds, expected_exit_code,
                  crash_at_pct, slow_multiplier, assigned_agent_id,
                  assigned_agent_epoch, attempt_count, max_attempts,
                  submitter, created_at, started_at, completed_at, insertion_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(job.id), job.product, job.status.value,
                    job.duration_seconds, job.expected_exit_code,
                    job.crash_at_pct, job.slow_multiplier,
                    str(job.assigned_agent_id) if job.assigned_agent_id else None,
                    job.assigned_agent_epoch, job.attempt_count, job.max_attempts,
                    job.submitter,
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
```

Update `get()` to fetch events:

```python
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
                    at=self._parse_iso(row["at"]),  # type: ignore[arg-type]
                    kind=row["kind"],
                    agent_id=UUID(row["agent_id"]) if row["agent_id"] else None,
                    agent_name=row["agent_name"],
                    detail=row["detail"],
                )
            )
        return events
```

- [ ] **Step 4: Run to verify it passes**

```
.venv/bin/pytest tests/unit/test_sqlite_store.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```
git add tss/server/sqlite_store.py tests/unit/test_sqlite_store.py
git commit -m "$(cat <<'EOF'
feat: SQLiteJobStore persists JobEvent history alongside jobs

Events are inserted in the same transaction as the job. INSERT OR IGNORE
handles the case where update() is called and the same events are present
again — only new events land. Round-trip test asserts both jobs and their
history survive the storage layer.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `SQLiteJobStore.update()` — persist mutations + new events

**Files:**
- Modify: `tss/server/sqlite_store.py`
- Modify: `tests/unit/test_sqlite_store.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

```
.venv/bin/pytest tests/unit/test_sqlite_store.py -v
```

Expected: `update()` not yet defined → FAIL.

- [ ] **Step 3: Implement `update()`**

Append to `tss/server/sqlite_store.py`:

```python
    def update(self, job: Job) -> None:
        with self._conn:
            self._conn.execute(
                """UPDATE jobs SET
                   product = ?, status = ?, duration_seconds = ?,
                   expected_exit_code = ?, crash_at_pct = ?, slow_multiplier = ?,
                   assigned_agent_id = ?, assigned_agent_epoch = ?,
                   attempt_count = ?, max_attempts = ?, submitter = ?,
                   started_at = ?, completed_at = ?
                   WHERE id = ?""",
                (
                    job.product, job.status.value, job.duration_seconds,
                    job.expected_exit_code, job.crash_at_pct, job.slow_multiplier,
                    str(job.assigned_agent_id) if job.assigned_agent_id else None,
                    job.assigned_agent_epoch, job.attempt_count, job.max_attempts,
                    job.submitter,
                    self._iso(job.started_at), self._iso(job.completed_at),
                    str(job.id),
                ),
            )
            # New events are appended to history in memory; INSERT OR IGNORE
            # de-dupes against the (job_id, at, kind) primary key so existing
            # events are not re-inserted.
            self._insert_events(job)
```

- [ ] **Step 4: Run to verify it passes**

```
.venv/bin/pytest tests/unit/test_sqlite_store.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```
git add tss/server/sqlite_store.py tests/unit/test_sqlite_store.py
git commit -m "$(cat <<'EOF'
feat: SQLiteJobStore update() persists mutations + new events

Implements the contract the dispatcher already calls (Task 3). Handles
idempotent re-update via INSERT OR IGNORE on the events composite key.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: `SQLiteJobStore` — remaining query methods

**Files:**
- Modify: `tss/server/sqlite_store.py`
- Modify: `tests/unit/test_sqlite_store.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

```
.venv/bin/pytest tests/unit/test_sqlite_store.py -v
```

Expected: FAILs — methods not yet implemented.

- [ ] **Step 3: Implement query methods**

Append to `tss/server/sqlite_store.py`:

```python
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
```

- [ ] **Step 4: Run to verify they pass**

```
.venv/bin/pytest tests/unit/test_sqlite_store.py -v
```

Expected: all PASS.

- [ ] **Step 5: Run all tests to confirm nothing broke**

```
.venv/bin/pytest -m "not chaos" -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```
git add tss/server/sqlite_store.py tests/unit/test_sqlite_store.py
git commit -m "$(cat <<'EOF'
feat: SQLiteJobStore queries — all, by_status, find_queued, iter, len

Insertion order is the FIFO contract the dispatcher relies on; preserved
via the insertion_order column we already write in add().

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Wire `SQLiteJobStore` as the default; migrate test fixture

**Files:**
- Modify: `tss/server/dispatcher.py` (default DI changes; same for `app.py` if needed)
- Modify: `tss/server/app.py` (verify dispatcher creation path)
- Modify: `tests/conftest.py` (the `dispatcher` fixture builds the dispatcher with a SQLite-`:memory:` store)
- Modify: `tss/cli.py` (`--db-path` flag on `serve`)

- [ ] **Step 1: Update `Dispatcher.__init__` default to use `SQLiteJobStore(":memory:")`**

In `tss/server/dispatcher.py`, change the default:

```python
from tss.server.sqlite_store import SQLiteJobStore  # add this import

class Dispatcher:
    def __init__(
        self,
        *,
        registry: AgentRegistry | None = None,
        store: JobStore | None = None,
        ...
    ) -> None:
        self.registry: AgentRegistry = registry or InMemoryAgentRegistry()
        # Default to in-memory SQLite for tests + lightweight dev usage. Production
        # callers (app factory) construct SQLiteJobStore("./tss.db") explicitly.
        self.store: JobStore = store or SQLiteJobStore(":memory:")
        ...
```

Remove the `from tss.server.store import InMemoryJobStore` import if it was being used as the default.

- [ ] **Step 2: Update the FastAPI app factory to use a file-backed SQLite by default**

In `tss/server/app.py`, update `create_app`:

```python
import os
...
from tss.server.sqlite_store import SQLiteJobStore

def create_app(
    *,
    dispatcher: Dispatcher | None = None,
    start_watchdog: bool = True,
    db_path: str | None = None,
) -> FastAPI:
    if dispatcher is None:
        path = db_path or os.environ.get("TSS_DB_PATH", "./tss.db")
        dispatcher = Dispatcher(store=SQLiteJobStore(path))
    disp = dispatcher
    watchdog = Watchdog(disp)
    ...
```

- [ ] **Step 3: Update `tss/cli.py` `serve` command to forward `--db-path`**

Find the `serve` command. Add:

```python
@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8080, "--port"),
    db_path: str = typer.Option("./tss.db", "--db-path", help="SQLite path. ':memory:' for ephemeral."),
) -> None:
    import uvicorn
    from tss.server.app import create_app
    app_instance = create_app(db_path=db_path)
    uvicorn.run(app_instance, host=host, port=port)
```

(Match the existing `serve` command's structure; the additions are the `db_path` parameter and passing it to `create_app`.)

- [ ] **Step 4: Run all tests to verify the dispatcher fixture still works**

```
.venv/bin/pytest -m "not chaos" -v
```

Expected: all green. (The default `Dispatcher()` now uses SQLite in-memory.)

- [ ] **Step 5: Commit**

```
git add tss/server/dispatcher.py tss/server/app.py tss/cli.py
git commit -m "$(cat <<'EOF'
feat: SQLiteJobStore is the default; CLI serve accepts --db-path

Dispatcher() defaults to in-memory SQLite for tests. The FastAPI app
factory uses ./tss.db by default; --db-path :memory: keeps the demo's
ephemeral behavior if desired.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Race tests + chaos test on SQLite — verify and fix

**Files:**
- Possibly modify: `tss/server/sqlite_store.py` if any concurrency issue surfaces

- [ ] **Step 1: Run all race tests**

```
.venv/bin/pytest tests/integration/test_concurrent_claim.py tests/integration/test_stale_agent.py tests/integration/test_per_job_overrun.py -v
```

Expected: all green. If any fail, debug — likely culprits:
- Connection lifetime issues (single connection should suffice; `check_same_thread=False` is on)
- Date round-trip differences (timezone handling in `_iso` / `_parse_iso`)
- Insertion order (is `find_queued_for_capabilities` returning the oldest? sort is `ORDER BY insertion_order ASC`)

- [ ] **Step 2: Run the chaos test**

```
.venv/bin/pytest -m chaos -v
```

Expected: green. If it fails:
- Look at the failure mode. SQLite locking errors look like `sqlite3.OperationalError: database is locked` — but our single-connection model serialized via `asyncio.Lock` should not produce these.
- If the failure is not about locking but about ordering or events, fix in `SQLiteJobStore`.
- DO NOT add a parallel in-memory implementation. Per the spec, we debug and fix.

- [ ] **Step 3: Run the full suite (incl. chaos) one more time as a smoke check**

```
.venv/bin/pytest -v
```

Expected: all green.

- [ ] **Step 4: Commit any fixes that were needed**

If fixes were applied to `tss/server/sqlite_store.py`:

```
git add tss/server/sqlite_store.py
git commit -m "$(cat <<'EOF'
fix(sqlite-store): <specific issue found and fixed>

<2-3 sentence explanation of what failed and why the fix is correct>

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

If no fixes were needed, skip — proceed to Task 11.

---

## Task 11: Delete `InMemoryJobStore`

**Files:**
- Modify: `tss/server/store.py` (remove `InMemoryJobStore`)
- Modify: any tests that import it (replace with the SQLite-default `Dispatcher`)

- [ ] **Step 1: Remove `InMemoryJobStore` from `tss/server/store.py`**

Edit `tss/server/store.py`. The Protocol stays. The class is deleted. Final content:

```python
"""Job store — pure data, no locking.

Jobs are kept in submission order so the queue is naturally FIFO. The store
exposes only the queries the Dispatcher needs; complex reporting is built on
top of ``all()`` rather than baked in here.

Implementations:
- ``SQLiteJobStore`` in ``tss.server.sqlite_store`` — canonical, used in
  production and tests (``:memory:`` mode for tests).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Protocol
from uuid import UUID

from tss.common.models import Job, JobStatus


class JobStore(Protocol):
    def add(self, job: Job) -> None: ...
    def update(self, job: Job) -> None: ...
    def get(self, job_id: UUID) -> Job | None: ...
    def all(self) -> list[Job]: ...
    def by_status(self, status: JobStatus) -> list[Job]: ...
    def find_queued_for_capabilities(self, capabilities: Iterable[str]) -> Job | None: ...
    def __iter__(self) -> Iterator[Job]: ...
    def __len__(self) -> int: ...
```

- [ ] **Step 2: Find every test importing `InMemoryJobStore` and update**

Search:

```
.venv/bin/python -c "import subprocess; subprocess.run(['grep', '-rn', 'InMemoryJobStore', 'tss', 'tests'])" \
  || grep -rn "InMemoryJobStore" tss tests
```

For each test that imports it, switch to constructing `Dispatcher()` (which now defaults to SQLite `:memory:`) or to constructing `SQLiteJobStore(":memory:")` directly. Most tests likely use `Dispatcher()` — those need no change. The ones in `tests/unit/test_dispatcher_persistence.py` (Task 3) extend `InMemoryJobStore`; rewrite the `CountingStore` to extend `SQLiteJobStore`:

```python
from tss.server.sqlite_store import SQLiteJobStore


class CountingStore(SQLiteJobStore):
    def __init__(self) -> None:
        super().__init__(":memory:")
        self.update_calls: list[UUID] = []

    def update(self, job: Job) -> None:
        super().update(job)
        self.update_calls.append(job.id)
```

Remove any other references.

- [ ] **Step 3: Run the full test suite**

```
.venv/bin/pytest -v
```

Expected: all green.

- [ ] **Step 4: Commit**

```
git add tss/server/store.py tests/
git commit -m "$(cat <<'EOF'
refactor: drop InMemoryJobStore — SQLiteJobStore is the only implementation

Spec §7.1: SQLite is canonical; tests use ":memory:" mode via the same
implementation. The JobStore Protocol remains so PostgresJobStore can slot
in via the same contract at scale.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: API — `?submitter=` query filter on `/api/jobs`

**Files:**
- Modify: `tss/server/routes/jobs.py`
- Modify: `tests/integration/test_submitter_filter.py`

- [ ] **Step 1: Write the failing integration test**

Append to `tests/integration/test_submitter_filter.py`:

```python
@pytest.mark.asyncio
async def test_submitter_query_filters_results(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    await dispatcher.submit_job(product="vehicle_gateway", duration_seconds=2.0, submitter="alice")
    await dispatcher.submit_job(product="vehicle_gateway", duration_seconds=2.0, submitter="alice")
    await dispatcher.submit_job(product="asset_gateway", duration_seconds=2.0, submitter="bob")

    async for client in http_client_factory(dispatcher):
        resp = await client.get("/api/jobs", params={"submitter": "alice"})
        assert resp.status_code == 200
        bodies = resp.json()
        assert len(bodies) == 2
        assert all(j["submitter"] == "alice" for j in bodies)


@pytest.mark.asyncio
async def test_submitter_combines_with_other_filters(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    await dispatcher.submit_job(product="vehicle_gateway", duration_seconds=2.0, submitter="alice")
    await dispatcher.submit_job(product="asset_gateway", duration_seconds=2.0, submitter="alice")
    async for client in http_client_factory(dispatcher):
        resp = await client.get(
            "/api/jobs",
            params={"submitter": "alice", "product": "vehicle_gateway"},
        )
        assert resp.status_code == 200
        bodies = resp.json()
        assert len(bodies) == 1
        assert bodies[0]["product"] == "vehicle_gateway"
```

- [ ] **Step 2: Run to verify it fails**

```
.venv/bin/pytest tests/integration/test_submitter_filter.py -v
```

Expected: FAIL — `submitter` query param not implemented.

- [ ] **Step 3: Add `submitter` to the route**

In `tss/server/routes/jobs.py`, update `list_jobs`:

```python
@router.get("/jobs", response_model=list[Job])
async def list_jobs(
    request: Request,
    status_filter: JobStatus | None = None,
    product: str | None = None,
    submitter: str | None = None,
) -> list[Job]:
    d = _disp(request)
    all_jobs = list(d.store)
    if status_filter is not None:
        all_jobs = [j for j in all_jobs if j.status == status_filter]
    if product is not None:
        all_jobs = [j for j in all_jobs if j.product == product]
    if submitter is not None:
        all_jobs = [j for j in all_jobs if j.submitter == submitter]
    return all_jobs
```

- [ ] **Step 4: Run to verify it passes**

```
.venv/bin/pytest tests/integration/test_submitter_filter.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```
git add tss/server/routes/jobs.py tests/integration/test_submitter_filter.py
git commit -m "$(cat <<'EOF'
feat: GET /api/jobs?submitter= filters by submitter

Combines with the existing status_filter and product params. Drives the
"Mine" filter on the dashboard (next task block).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: API — `GET /api/jobs/{job_id}` endpoint

**Files:**
- Modify: `tss/server/routes/jobs.py`
- Create: `tests/integration/test_job_detail.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_job_detail.py`:

```python
"""Integration test for GET /api/jobs/{job_id}."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tss.server.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_get_job_by_id_returns_full_record(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    job = await dispatcher.submit_job(
        product="vehicle_gateway", duration_seconds=8.0, submitter="alice",
    )
    async for client in http_client_factory(dispatcher):
        resp = await client.get(f"/api/jobs/{job.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(job.id)
        assert body["submitter"] == "alice"
        assert body["product"] == "vehicle_gateway"
        # History is populated — at least the "submitted" event
        assert len(body["history"]) >= 1
        assert body["history"][0]["kind"] == "submitted"


@pytest.mark.asyncio
async def test_get_job_by_id_returns_404_for_unknown(
    dispatcher: Dispatcher,
    http_client_factory,
) -> None:
    async for client in http_client_factory(dispatcher):
        resp = await client.get(f"/api/jobs/{uuid4()}")
        assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

```
.venv/bin/pytest tests/integration/test_job_detail.py -v
```

Expected: FAIL — endpoint not implemented (likely returns the route catch-all or 404 from FastAPI's path matching).

- [ ] **Step 3: Add the endpoint**

In `tss/server/routes/jobs.py`, after the `list_jobs` handler:

```python
@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: UUID, request: Request) -> Job:
    d = _disp(request)
    job = d.store.get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"job={job_id} not found")
    return job
```

- [ ] **Step 4: Run to verify it passes**

```
.venv/bin/pytest tests/integration/test_job_detail.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```
git add tss/server/routes/jobs.py tests/integration/test_job_detail.py
git commit -m "$(cat <<'EOF'
feat: GET /api/jobs/{job_id} returns full Job + history

Source for the dashboard's job detail panel.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: `/metrics` endpoint — hand-rolled Prometheus text format

**Files:**
- Create: `tss/server/routes/metrics.py`
- Modify: `tss/server/app.py` (register the router)
- Create: `tests/integration/test_metrics.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_metrics.py`:

```python
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
        # Core metrics must be present
        assert "tss_jobs_queued" in body
        assert "tss_jobs_running" in body
        assert "tss_agents_total" in body
        # # HELP and # TYPE comments per Prometheus exposition format
        assert "# HELP tss_jobs_queued" in body
        assert "# TYPE tss_jobs_queued gauge" in body
```

- [ ] **Step 2: Run to verify it fails**

```
.venv/bin/pytest tests/integration/test_metrics.py -v
```

Expected: FAIL — endpoint doesn't exist.

- [ ] **Step 3: Create the metrics route**

Create `tss/server/routes/metrics.py`:

```python
"""Hand-rolled Prometheus text-format /metrics endpoint.

Wired in for the demo and the Section 4 talking point. We do not depend on
prometheus-client to keep the dependency surface zero. Format reference:
https://prometheus.io/docs/instrumenting/exposition_formats/#text-based-format

Uses ``Dispatcher.snapshot_fleet`` so counts are taken under the dispatcher's
lock — coherent and consistent with what the dashboard sees.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from tss.server.dispatcher import Dispatcher

router = APIRouter(tags=["metrics"])


def _disp(request: Request) -> Dispatcher:
    return request.app.state.dispatcher  # type: ignore[no-any-return]


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    d = _disp(request)
    snap = await d.snapshot_fleet()
    s = snap.stats

    lines = [
        "# HELP tss_jobs_queued Number of jobs currently in the queue.",
        "# TYPE tss_jobs_queued gauge",
        f"tss_jobs_queued {s.queue_depth}",
        "# HELP tss_jobs_running Number of jobs currently running on agents.",
        "# TYPE tss_jobs_running gauge",
        f"tss_jobs_running {s.jobs_running}",
        "# HELP tss_jobs_completed_total Total jobs that reached COMPLETED.",
        "# TYPE tss_jobs_completed_total counter",
        f"tss_jobs_completed_total {s.jobs_completed}",
        "# HELP tss_jobs_failed_total Total jobs that reached FAILED.",
        "# TYPE tss_jobs_failed_total counter",
        f"tss_jobs_failed_total {s.jobs_failed}",
        "# HELP tss_agents_total Number of registered agents by status.",
        "# TYPE tss_agents_total gauge",
        f'tss_agents_total{{status="idle"}} {s.idle}',
        f'tss_agents_total{{status="busy"}} {s.busy}',
        f'tss_agents_total{{status="offline"}} {s.offline}',
    ]
    body = "\n".join(lines) + "\n"
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")
```

- [ ] **Step 4: Register the router in `tss/server/app.py`**

After the existing `app.include_router(...)` calls, add:

```python
from tss.server.routes import metrics as metrics_routes
...
    app.include_router(metrics_routes.router)
```

- [ ] **Step 5: Run to verify it passes**

```
.venv/bin/pytest tests/integration/test_metrics.py -v
```

Expected: PASS.

- [ ] **Step 6: Manually smoke-test**

```
make demo-stop
.venv/bin/tss serve --db-path :memory: --port 8090 &
sleep 2
curl -s http://127.0.0.1:8090/metrics | head -30
kill %1
```

Expected: Prometheus text output with `# HELP` / `# TYPE` lines and metric values.

- [ ] **Step 7: Commit**

```
git add tss/server/routes/metrics.py tss/server/app.py tests/integration/test_metrics.py
git commit -m "$(cat <<'EOF'
feat: /metrics endpoint in Prometheus text format (no deps)

Wires the Section 4 talking-point: the wiring is in, no Grafana stack
to run during the demo.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Dashboard — identity prompt banner + localStorage

**Files:**
- Modify: `tss/server/static/index.html` (add the banner element)
- Modify: `tss/server/static/style.css` (banner styles)
- Modify: `tss/server/static/app.js` (read/write `localStorage["tss.submitter"]`, show/hide banner)

- [ ] **Step 1: Add the banner HTML to `tss/server/static/index.html`**

Inside `<body>` at the very top, before the `<header class="topbar">`:

```html
<div id="identity-banner" class="identity-banner" hidden>
  <span>Tell us your name to filter jobs you submit:</span>
  <input id="identity-input" type="text" maxlength="40" placeholder="e.g. jackson" autocomplete="off" />
  <button id="identity-save" type="button">save</button>
</div>
<div id="identity-pill" class="identity-pill" hidden>
  <span>as: <span id="identity-name"></span></span>
  <button id="identity-change" type="button" title="Change">change</button>
</div>
```

The pill goes in the top bar; we'll move it later in CSS via `position: absolute` or by placing the markup elsewhere. For now keep it under the banner.

- [ ] **Step 2: Add styles to `tss/server/static/style.css`**

Append:

```css
.identity-banner {
  display: flex;
  gap: .75rem;
  align-items: center;
  padding: .75rem 1rem;
  background: var(--bg-elev, #f6f8fa);
  border-bottom: 1px solid var(--border, #e1e4e8);
  font-size: .9rem;
}
.identity-banner input {
  font: inherit;
  padding: .35rem .5rem;
  border: 1px solid var(--border, #d0d7de);
  border-radius: 4px;
  flex: 0 1 16rem;
}
.identity-banner button,
.identity-pill button {
  font: inherit;
  padding: .35rem .75rem;
  border: 1px solid var(--border, #d0d7de);
  border-radius: 4px;
  background: var(--btn, #fff);
  cursor: pointer;
}
.identity-banner button:hover,
.identity-pill button:hover { background: var(--btn-hover, #f0f3f6); }
.identity-pill {
  display: flex;
  gap: .5rem;
  align-items: center;
  padding: .25rem .75rem;
  background: var(--bg-elev, #f6f8fa);
  border-bottom: 1px solid var(--border, #e1e4e8);
  font-size: .85rem;
  color: var(--muted, #57606a);
}
```

(Reuse the codebase's existing CSS variable names — adjust `var(--...)` names to match what `style.css` already defines.)

- [ ] **Step 3: Add the JS logic to `tss/server/static/app.js`**

At the top of the file (before existing logic):

```js
// ---- Identity (submitter) management ----
const STORAGE_KEY = "tss.submitter";

function loadSubmitter() {
  try {
    return localStorage.getItem(STORAGE_KEY) || "";
  } catch (e) {
    return "";
  }
}

function saveSubmitter(name) {
  try {
    localStorage.setItem(STORAGE_KEY, name);
  } catch (e) {
    /* localStorage disabled — accept and continue */
  }
  refreshIdentityUI();
}

function refreshIdentityUI() {
  const banner = document.getElementById("identity-banner");
  const pill = document.getElementById("identity-pill");
  const nameEl = document.getElementById("identity-name");
  const submitter = loadSubmitter();
  if (submitter) {
    banner.hidden = true;
    pill.hidden = false;
    if (nameEl) nameEl.textContent = submitter;
  } else {
    banner.hidden = false;
    pill.hidden = true;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  refreshIdentityUI();
  const saveBtn = document.getElementById("identity-save");
  const input = document.getElementById("identity-input");
  if (saveBtn && input) {
    saveBtn.addEventListener("click", () => {
      const value = input.value.trim();
      if (value) saveSubmitter(value);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") saveBtn.click();
    });
  }
  const changeBtn = document.getElementById("identity-change");
  if (changeBtn) {
    changeBtn.addEventListener("click", () => {
      const name = prompt("Update your name:", loadSubmitter());
      if (name !== null) saveSubmitter(name.trim());
    });
  }
});
```

- [ ] **Step 4: Manual smoke test**

```
make demo-stop
make demo-plain
```

Open http://localhost:8080. Verify:
- Banner appears on first load
- After typing a name + clicking save, banner disappears
- "as: <name> [change]" pill appears
- Hard reload — pill persists
- Click change → prompt → enter new name → pill updates

- [ ] **Step 5: Commit**

```
git add tss/server/static/index.html tss/server/static/style.css tss/server/static/app.js
git commit -m "$(cat <<'EOF'
feat(dashboard): identity prompt banner + localStorage persistence

First piece of the customer-facing additions. Stores the submitter in
localStorage so the next pieces (Mine filter, completion toast) can
identify "my" jobs.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: Dashboard — *Mine* filter toggle

**Files:**
- Modify: `tss/server/static/index.html` (toggle near the queue/running tables)
- Modify: `tss/server/static/style.css` (toggle styles)
- Modify: `tss/server/static/app.js` (filter logic)

- [ ] **Step 1: Add the toggle HTML**

In `tss/server/static/index.html`, immediately above the queue/running section:

```html
<div class="filter-bar">
  <label class="toggle">
    <input id="mine-toggle" type="checkbox" />
    <span>Mine only</span>
  </label>
</div>
```

- [ ] **Step 2: CSS**

Append to `style.css`:

```css
.filter-bar {
  display: flex;
  gap: 1rem;
  padding: .5rem 1rem;
  align-items: center;
  font-size: .9rem;
  color: var(--muted, #57606a);
}
.toggle {
  display: inline-flex;
  align-items: center;
  gap: .4rem;
  cursor: pointer;
  user-select: none;
}
.toggle input { cursor: pointer; }
```

- [ ] **Step 3: JS — filter logic**

In `app.js`, find where queue/running tables are rendered. Wrap the rendered list with a filter:

```js
// At the top with other helpers
function isMineOn() {
  const t = document.getElementById("mine-toggle");
  return t ? t.checked : false;
}

function applySubmitterFilter(jobs) {
  if (!isMineOn()) return jobs;
  const mine = loadSubmitter();
  if (!mine) return jobs;
  return jobs.filter((j) => j.submitter === mine);
}
```

Wherever the rendering function does `data.queue.forEach(...)` or `data.running_jobs.forEach(...)`, change to `applySubmitterFilter(data.queue).forEach(...)` etc.

Default the toggle to checked when a submitter is set:

```js
// Inside DOMContentLoaded after refreshIdentityUI()
const mineToggle = document.getElementById("mine-toggle");
if (mineToggle) {
  mineToggle.checked = !!loadSubmitter();
  mineToggle.addEventListener("change", () => {
    // Force a re-render on next poll (or trigger immediately if poll handle is exposed)
    if (typeof renderFleet === "function") {
      const lastData = window.__lastFleetData;
      if (lastData) renderFleet(lastData);
    }
  });
}
```

(If the existing `app.js` exposes its render function under a different name, adapt to that. The intent is: when the toggle changes, re-render with the existing cached data.)

- [ ] **Step 4: Manual smoke test**

Reload dashboard. Submit two jobs as "alice" via:

```
.venv/bin/tss submit-job --product vehicle_gateway --duration 4 --submitter alice
.venv/bin/tss submit-job --product asset_gateway --duration 4 --submitter bob
```

Set identity to "alice", verify only alice's job is shown when toggle is on; both shown when off.

- [ ] **Step 5: Commit**

```
git add tss/server/static/index.html tss/server/static/style.css tss/server/static/app.js
git commit -m "$(cat <<'EOF'
feat(dashboard): Mine-only filter toggle for queue + running tables

Filters client-side from the polling response. Agent tiles are not
filtered; operators always see the whole fleet.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 17: Dashboard — Job detail side panel

**Files:**
- Modify: `tss/server/static/index.html` (panel markup)
- Modify: `tss/server/static/style.css` (slide-in styles)
- Modify: `tss/server/static/app.js` (open/close, fetch, render)

- [ ] **Step 1: Add panel markup**

In `index.html`, before `</body>`:

```html
<aside id="job-panel" class="job-panel" hidden>
  <header class="job-panel-header">
    <h2 id="job-panel-title">Job</h2>
    <button id="job-panel-close" type="button" aria-label="Close">×</button>
  </header>
  <section class="job-panel-meta">
    <div><strong>id</strong><code id="job-panel-id"></code></div>
    <div><strong>product</strong><span id="job-panel-product"></span></div>
    <div><strong>status</strong><span id="job-panel-status"></span></div>
    <div><strong>submitter</strong><span id="job-panel-submitter"></span></div>
    <div><strong>attempts</strong><span id="job-panel-attempts"></span></div>
    <div><strong>agent</strong><span id="job-panel-agent"></span></div>
  </section>
  <section class="job-panel-history">
    <h3>History</h3>
    <ol id="job-panel-events"></ol>
  </section>
  <details class="job-panel-raw">
    <summary>Raw payload</summary>
    <pre id="job-panel-raw"></pre>
  </details>
</aside>
```

- [ ] **Step 2: CSS**

Append:

```css
.job-panel {
  position: fixed;
  top: 0; right: 0; bottom: 0;
  width: min(460px, 90vw);
  background: var(--bg, #fff);
  border-left: 1px solid var(--border, #e1e4e8);
  box-shadow: -4px 0 16px rgba(0,0,0,.08);
  overflow-y: auto;
  padding: 1rem;
  z-index: 1000;
  transform: translateX(0);
  transition: transform .2s ease;
}
.job-panel[hidden] { display: block; transform: translateX(100%); pointer-events: none; }
.job-panel-header {
  display: flex; align-items: center; justify-content: space-between;
  border-bottom: 1px solid var(--border, #e1e4e8);
  padding-bottom: .5rem; margin-bottom: 1rem;
}
.job-panel-header h2 { margin: 0; font-size: 1.1rem; }
.job-panel-header button {
  font-size: 1.5rem; line-height: 1; background: none; border: none; cursor: pointer;
}
.job-panel-meta { display: grid; grid-template-columns: 1fr 2fr; gap: .35rem .75rem; font-size: .9rem; }
.job-panel-meta strong { color: var(--muted, #57606a); font-weight: 500; }
.job-panel-meta code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .8rem; }
.job-panel-history { margin-top: 1.25rem; }
.job-panel-history h3 { font-size: 1rem; margin: 0 0 .5rem; }
.job-panel-history ol { list-style: none; padding-left: 0; margin: 0; }
.job-panel-history li { border-left: 2px solid var(--border, #d0d7de); padding: .25rem .75rem; font-size: .85rem; }
.job-panel-raw { margin-top: 1.25rem; font-size: .85rem; }
.job-panel-raw pre {
  background: var(--bg-elev, #f6f8fa); padding: .75rem; border-radius: 4px;
  font-size: .75rem; overflow-x: auto;
}
```

- [ ] **Step 3: JS — open/close, fetch, render**

In `app.js`:

```js
// ---- Job detail panel ----
const panel = () => document.getElementById("job-panel");

async function openJobPanel(jobId) {
  try {
    const resp = await fetch(`/api/jobs/${jobId}`);
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    const job = await resp.json();
    renderJobPanel(job);
    const p = panel();
    if (p) p.hidden = false;
  } catch (e) {
    console.error("failed to load job", jobId, e);
  }
}

function closeJobPanel() {
  const p = panel();
  if (p) p.hidden = true;
}

function renderJobPanel(job) {
  document.getElementById("job-panel-title").textContent = `Job ${job.id.slice(0, 8)}`;
  document.getElementById("job-panel-id").textContent = job.id;
  document.getElementById("job-panel-product").textContent = job.product;
  document.getElementById("job-panel-status").textContent = job.status;
  document.getElementById("job-panel-submitter").textContent = job.submitter;
  document.getElementById("job-panel-attempts").textContent =
    `${job.attempt_count} of ${job.max_attempts}`;
  document.getElementById("job-panel-agent").textContent =
    job.assigned_agent_id || "—";
  const events = document.getElementById("job-panel-events");
  events.innerHTML = "";
  for (const e of job.history) {
    const li = document.createElement("li");
    const at = new Date(e.at).toLocaleTimeString();
    const tail = e.detail ? ` — ${e.detail}` : "";
    const agent = e.agent_name ? ` [${e.agent_name}]` : "";
    li.textContent = `${at}  ${e.kind}${agent}${tail}`;
    events.appendChild(li);
  }
  document.getElementById("job-panel-raw").textContent =
    JSON.stringify(job, null, 2);
}

document.addEventListener("DOMContentLoaded", () => {
  const close = document.getElementById("job-panel-close");
  if (close) close.addEventListener("click", closeJobPanel);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeJobPanel();
  });
});
```

Then attach click handlers to job rows. Find where queue/running rows are rendered. After each row creation, add:

```js
row.dataset.jobId = job.id;
row.addEventListener("click", () => openJobPanel(job.id));
row.style.cursor = "pointer";
```

(If rows are rendered as `<tr>`, that's the element. Adapt to existing markup.)

- [ ] **Step 4: Manual smoke test**

Reload, submit a job, click the row. Panel slides in. Verify:
- Header shows job ID prefix
- All meta fields populated
- History list shows at least the "submitted" event
- Raw payload renders as JSON
- Esc closes the panel
- × button closes the panel

- [ ] **Step 5: Commit**

```
git add tss/server/static/index.html tss/server/static/style.css tss/server/static/app.js
git commit -m "$(cat <<'EOF'
feat(dashboard): job detail side panel — full history + raw payload

Click any row in the queue / running tables to open a slide-in panel
with the job's metadata, JobEvent history, and the raw JSON. The history
view is what answers "where did my job run, why did it fail?" for the
firmware engineer persona.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 18: Dashboard — Browser Notification toast on completion of "my" jobs

**Files:**
- Modify: `tss/server/static/app.js`

- [ ] **Step 1: Implement permission + diff detection**

Append to `app.js`:

```js
// ---- Completion notifications ----
let notifyPermission = "default";
let lastJobStatusById = new Map(); // jobId -> previous status

function requestNotifyPermission() {
  if (!("Notification" in window)) return;
  if (Notification.permission === "default") {
    Notification.requestPermission().then((p) => { notifyPermission = p; });
  } else {
    notifyPermission = Notification.permission;
  }
}

function notifyTerminal(job) {
  const submitter = loadSubmitter();
  if (!submitter || job.submitter !== submitter) return;
  const title = "TSS";
  const body = job.status === "completed"
    ? `Job ${job.id.slice(0, 8)} completed`
    : `Job ${job.id.slice(0, 8)} failed`;
  if ("Notification" in window && Notification.permission === "granted") {
    new Notification(title, { body });
  } else {
    flashBanner(body);
  }
}

function flashBanner(message) {
  const el = document.createElement("div");
  el.className = "flash-banner";
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

function checkForCompletions(allJobs) {
  for (const job of allJobs) {
    const prev = lastJobStatusById.get(job.id);
    if (prev && prev !== job.status && (job.status === "completed" || job.status === "failed")) {
      notifyTerminal(job);
    }
    lastJobStatusById.set(job.id, job.status);
  }
}
```

In CSS, add a minimal `.flash-banner`:

```css
.flash-banner {
  position: fixed; top: 1rem; right: 1rem;
  background: var(--bg-elev, #f6f8fa);
  border: 1px solid var(--border, #d0d7de);
  border-radius: 4px;
  padding: .75rem 1rem;
  box-shadow: 0 4px 12px rgba(0,0,0,.08);
  font-size: .9rem;
  z-index: 1500;
}
```

- [ ] **Step 2: Wire `checkForCompletions` into the polling loop**

Find the function that processes `/api/fleet/status` (or `/api/jobs`) responses. Add a call to fetch all jobs once per cycle (or lift from the fleet status response if running jobs + recent events expose enough). Simplest: add a separate `fetchAllJobs()` call inside the existing polling loop.

```js
async function pollForCompletions() {
  const submitter = loadSubmitter();
  if (!submitter) return;
  try {
    const resp = await fetch(`/api/jobs?submitter=${encodeURIComponent(submitter)}`);
    if (!resp.ok) return;
    const jobs = await resp.json();
    checkForCompletions(jobs);
  } catch (e) {
    /* swallow — best-effort */
  }
}
```

Inside the existing 1s-interval polling callback, also call `pollForCompletions()`.

- [ ] **Step 3: Request permission when identity is saved**

In the existing `saveSubmitter` flow, after successful save, call `requestNotifyPermission()`:

```js
function saveSubmitter(name) {
  try {
    localStorage.setItem(STORAGE_KEY, name);
  } catch (e) {}
  refreshIdentityUI();
  requestNotifyPermission();
}
```

- [ ] **Step 4: Manual smoke test**

```
make demo-stop
make demo-plain
```

In the browser:
1. Set identity = "alice" — browser prompts for notification permission, accept.
2. In a separate terminal:
   ```
   .venv/bin/tss submit-job --product vehicle_gateway --duration 4 --submitter alice
   ```
3. Wait ~5 seconds. A native browser notification should fire when the job completes.
4. Test fallback: deny notification permission in browser settings, repeat. Flash banner should appear instead.

- [ ] **Step 5: Commit**

```
git add tss/server/static/app.js tss/server/static/style.css
git commit -m "$(cat <<'EOF'
feat(dashboard): browser notifications when one of "my" jobs finishes

Uses the native Notification API for granted permissions; falls back to
an in-page flash banner if denied or unsupported. Diff detection driven
from the existing 1s polling loop, scoped to the current submitter.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 19: Final verification + smoke test

**Files:** none

- [ ] **Step 1: Run the full test suite**

```
.venv/bin/pytest -v
```

Expected: all green (race tests, chaos test, new tests).

- [ ] **Step 2: Run mypy + ruff**

```
.venv/bin/mypy tss
.venv/bin/ruff check tss tests
.venv/bin/ruff format --check tss tests
```

Expected: clean.

- [ ] **Step 3: End-to-end smoke test on the demo machine**

```
make demo-stop
make demo-plain
```

Manually walk through:
1. http://localhost:8080 loads, identity banner appears
2. Save name "jackson" — banner gone, pill shows "as: jackson"
3. Mine toggle defaults to ON
4. CLI: `.venv/bin/tss submit-job --product vehicle_gateway --duration 4`  → job appears in queue, then running, click row → detail panel slides in
5. CLI submit as someone else: `--submitter alex --product vehicle_gateway --duration 4` → with Mine ON it does NOT appear; toggle OFF, both appear
6. Wait for "your" job to complete → browser notification fires
7. http://localhost:8080/metrics returns Prometheus text
8. http://localhost:8080/api/jobs/<id> returns full Job + history
9. Click kill on an agent during a running job → reassignment visible in detail panel history
10. `make demo-stop` cleans up

- [ ] **Step 4: Final commit (if anything was tweaked)**

If the smoke test surfaced minor issues (CSS tweak, off-by-one in JS), commit as a tidy follow-up:

```
git add tss/server/static/
git commit -m "$(cat <<'EOF'
polish: <specific tweak from smoke test>

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Done.

Spec coverage check (self-review):

- ✅ §7.1 SQLite store + drop in-memory: Tasks 4–8, 11
- ✅ §7.2 submitter field: Task 1
- ✅ §7.3 /metrics endpoint: Task 14
- ✅ §7.4 API additions (`?submitter=`, `GET /api/jobs/{id}`): Tasks 12, 13
- ✅ §7.5 tests on `:memory:`: Task 9 (default DI), Tasks 1–13 (each test runs against this default)
- ✅ §8.1 identity prompt: Task 15
- ✅ §8.2 Mine filter: Task 16
- ✅ §8.3 Job detail panel: Task 17
- ✅ §8.4 Completion toast: Task 18
- ✅ §10 testing: covered by the per-task TDD pattern + Task 19 final verification
- ✅ §5 engineering posture: enforced throughout — no new deps, sync sqlite3, single connection, single asyncio.Lock unchanged
