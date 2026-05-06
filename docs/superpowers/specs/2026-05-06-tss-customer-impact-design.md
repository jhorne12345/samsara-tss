# Spec: TSS customer-impact upgrade — persistence + dashboard extensions

**Date:** 2026-05-06
**Status:** Drafted; pending user review
**Owner:** jackson@
**Driver:** Samsara "Building with AI" assessment, presenting in `~`days

---

## 1. Executive summary

Make the existing TSS measurably better for its customer — the firmware engineer running tests — by adding three things behind the existing JobStore seam and three things on the existing hand-rolled dashboard. No UI framework, no build step, no rewrites. Each line item must directly improve the customer's daily life or be near-free credibility (the metrics endpoint).

The deliberate pivot from an earlier draft of this spec is that **a full React rebuild was over-engineering against the stated filter** ("don't over-index on areas that aren't customer-impacting"). Polish is not customer impact. The vanilla dashboard already serves the operator persona; we extend it surgically for the customer persona.

## 2. Goals

In priority order:

1. **Customer impact.** A firmware engineer can find their tests, see why a test failed, and get notified when their test finishes — without a UI rebuild.
2. **Persistence.** Jobs survive a dispatcher restart. "My jobs from yesterday" is real.
3. **Engineering credibility.** The architecture choices stand up under scrutiny: the `JobStore` seam is demonstrable (real swap, no longer theoretical), and the metrics path is wired (live `/metrics` endpoint).
4. **Demo robustness.** Zero new infrastructure, zero new failure modes, zero new dependencies on the demo machine.

## 3. Non-goals

Explicit, so the spec is unambiguous about what is *not* being built:

- **No UI framework rebuild** (React, Vue, Svelte, etc.). The existing hand-rolled HTML/CSS/JS gets surgical additions.
- **No build step** (no Vite, no npm, no bundler).
- **No TypeScript layer.**
- **No new dashboard charts** (no `vis-timeline`, no Recharts, no D3).
- **No command palette, dark-mode toggle, or toast library** beyond the minimal browser-native `Notification` API.
- **No authentication or authorization** beyond an honor-system `submitter` field.
- **No Postgres / Redis** (sketched in `docs/scale-evolution.md`; not built).
- **No live Prometheus / Grafana / Alertmanager stack.** `/metrics` endpoint only — talking-point material for Section 4.
- **No SSE / WebSockets.** Polling matches the agent transport.
- **No mobile-responsive layout.**
- **No migrations framework** (Alembic). Single `CREATE TABLE IF NOT EXISTS` at startup.

## 4. Personas

| Persona | Role | What they need | Where served |
|---|---|---|---|
| **QA / firmware engineer** (the customer) | Submits a test, waits for result, debugs failures | "Where's my job? Did it pass? Why did it fail?" | Existing dashboard with a *Mine* filter, a job-detail panel, and a completion toast (§8) |
| **TSS operator** (the owner) | Keeps the dispatcher healthy, manages fleet | "Are rigs healthy? Is the queue backing up?" | Existing dashboard, unchanged |

One page, one URL. The filter toggle determines which persona's view is foregrounded — *Mine OFF* is the operator default, *Mine ON* is the firmware engineer default. The persona insight matters because it justifies the customer-side additions; it does not require a separate route or two SPAs.

## 5. Engineering posture — where we went hardcore vs simple

The load-bearing section. Every line is a deliberate decision with a stated reason. Reviewers should be able to attack any row.

### 5.1 Where we went hardcore (deliberate engineering rigor)

| Decision | Why it earns its rent here, today |
|---|---|
| **Single `asyncio.Lock` for all state mutations** in `Dispatcher` | The brief explicitly grades race conditions. The lock is *the* correctness story. A simpler "trust async" design would be incorrect, not just simpler. |
| **Epoch invariant on every Agent + `assigned_agent_epoch` on every Job** | The fencing-token pattern. Required for correctness under reassignment — without it, late results from a zombie agent can clobber the new agent's correct result. |
| **`JobStore` Protocol with one production implementation, demonstrably swappable** | The Protocol was already there; this spec replaces the only implementation behind it (`InMemoryJobStore` → `SQLiteJobStore`). That swap *is* the proof. The Section 4 scale story moves from theoretical to demonstrable. |
| **Job persistence in SQLite** | Single biggest customer-impact lift in this spec. Survives restart. Makes "My jobs from yesterday" a real feature. |
| **Append-only `JobEvent` history persisted alongside jobs** | The audit trail the customer-facing detail panel depends on. Without it, "why did my job fail / where did it run" is hand-waving. |
| **`mypy --strict` on Python; `tss/` type-clean** | Standard for this codebase. No silent `Any`s; new SQL/aiosqlite code is fully typed at its boundaries. |
| **Atomic claim under the existing lock — store change does NOT change the dispatcher's mutual-exclusion model** | When the store changes, the lock semantics do *not*. SQLite serializes writes at the connection; we keep `asyncio.Lock` because that's the contract the dispatcher already operates under. Two coordination layers > one is wrong; we keep the simpler one. |

### 5.2 Where we went simple (deliberate shortcuts — defensible)

| Decision | Why simple is right *here* | Production answer |
|---|---|---|
| **Honor-system `submitter` field** (localStorage on web, `$USER` on CLI) | Trusted internal HIL network; auth would be theatre at this scope; demonstrating customer impact ≠ demonstrating auth | mTLS + cert-bound identity; per-user permissions |
| **Single SQLite file, single connection, no pooling** | At ≤100 agents, contention is invisible. SQLite serializes writes at the connection. A pool now is premature. | Postgres + advisory locks; pgbouncer |
| **`CREATE TABLE IF NOT EXISTS` at startup, no Alembic** | One table at v0.1, one schema version. A migration framework now is ceremony for ceremony's sake. | Alembic with versioned migrations from v0.2 onward |
| **`AgentRegistry` stays in-memory** | Agents are *ephemeral by design*. When the dispatcher restarts, every agent re-registers anyway (epoch resets, heartbeat clock starts fresh). Persisting agent state would be more wrong than missing. | Same — agents stay ephemeral, but Redis stores liveness if dispatcher is sharded |
| **Hand-rolled HTML/CSS/JS dashboard, surgically extended** | The existing 290 LOC of JS + 530 LOC of CSS already serves the operator persona well. The customer-facing additions (mine filter, detail panel, completion toast) are additive and don't require a framework. A React rebuild would be polish, not customer impact, and adds a build step + bundle size + new failure modes for zero customer-facing return. | Eventually a real frontend stack — but not now, and not driven by a demo. |
| **Polling at 1s for fleet status; no SSE / WebSockets** | Matches the agent transport choice. HIL networks are flaky; reconnect logic is exactly where AI gets things wrong. | Push notifications via NATS or Redis pub/sub once cross-process state exists |
| **Browser `Notification` API for completion toasts (no library)** | Native, zero deps, works fine for the demo. The whole feature is one `new Notification(...)` call gated on permission. | Sonner / react-hot-toast in a real frontend |
| **Honor-system kill button on agent tiles** | Demo affordance. Anyone in the room can kill an agent. | Permissions, audit log, "are you sure" dialogs |

### 5.3 Where we deliberately did NOT add complexity

| Asked | Answer |
|---|---|
| Why no UI framework (React/Vue/Svelte)? | The customer-facing improvements are additive to the existing dashboard. A framework rebuild adds a build step, bundle, and new failure modes for *zero customer-impact return*. The vanilla path is the right size. |
| Why no TypeScript? | No frontend build step → no compile target → adds nothing here. The existing JS is small, focused, and well-typed at the boundary by Pydantic on the backend. |
| Why no `vis-timeline` / chart library on the operator view? | Operator polish, not customer impact. Defer to a follow-up if the operator persona ever becomes the primary user. |
| Why no `⌘K` command palette? | Pure polish; the existing dashboard is small enough that browse-and-click is fast. |
| Why no dark mode? | Polish. Default theme already reads well on screen-share. |
| Why no Docker / docker-compose? | Single Python process; adding container orchestration is for the scale story, not the demo. |
| Why no Sentry / OTel observability layer? | `/metrics` covers the credibility ask. Real observability lives in the scale-evolution path. |
| Why no GraphQL? | One process, one schema, one consumer. REST + OpenAPI is the right size. |
| Why a one-page dashboard instead of a routed `/jobs` SPA? | The customer's view is a filter, not a separate page. A routed SPA would be a rewrite that delivers no additional customer value. |

## 6. Architecture overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                  TSS Dispatcher (FastAPI process)                   │
│                                                                     │
│  ┌─────────────┐    ┌──────────────────────┐    ┌──────────────┐    │
│  │  Routes     │    │      Dispatcher      │    │   Watchdog   │    │
│  │  /api/*     │───▶│  (one asyncio.Lock)  │◀───│   (1s tick)  │    │
│  └─────────────┘    └──────────┬───────────┘    └──────────────┘    │
│         ▲                      │                                    │
│         │                      ▼                                    │
│  ┌─────────────┐         ┌─────────────┐    ┌────────────────────┐  │
│  │  /metrics   │         │AgentRegistry│    │ JobStore (Protocol)│  │
│  │ (Prometheus)│         │ (in-memory) │    │ ┌────────────────┐ │  │
│  └─────────────┘         └─────────────┘    │ │SQLiteJobStore  │ │  │
│         ▲                      ▲            │ │(canonical;     │ │  │
│         │                      │            │ │ tests use      │ │  │
│  ┌─────────────────────────────────┐        │ │ ":memory:")    │ │  │
│  │   Static dashboard              │        │ └────────────────┘ │  │
│  │   (existing tss/server/static/) │        └────────────────────┘  │
│  │   + Mine filter                 │                  │             │
│  │   + Job detail panel            │                  ▼             │
│  │   + Completion toast            │            ┌───────────┐       │
│  └─────────────────────────────────┘            │  tss.db   │       │
│                                                 └───────────┘       │
└─────────────────────────────────────────────────────────────────────┘
                               ▲
                               │  HTTP polling: register, heartbeat,
                               │  claim-next-job, report-result
                               │
       ┌───────────────────────┴────────────────────────┐
       │                                                │
   ┌───────┐  ┌───────┐  ┌───────┐  ┌───────┐  ┌───────────┐
   │ vg-01 │  │ vg-02 │  │ ag-01 │  │ ag-02 │  │ combo-01  │
   │  rig  │  │  rig  │  │  rig  │  │  rig  │  │   rig     │
   └───────┘  └───────┘  └───────┘  └───────┘  └───────────┘
```

Same dispatcher. Same lock semantics. Same epoch invariant. Same hand-rolled dashboard. New: persistence behind the existing seam, customer-facing dashboard additions, metrics endpoint.

## 7. Backend changes

### 7.1 `JobStore` — drop in-memory, add SQLite

```
tss/server/store.py
├── class JobStore (Protocol)        ← unchanged contract
├── class InMemoryJobStore           ← DELETED
└── class SQLiteJobStore (NEW)       ← canonical; tests use ":memory:"
```

Tests use `SQLiteJobStore(":memory:")` via a pytest fixture. **No chaos-test contingency.** If the chaos test surfaces SQLite-specific issues, we debug and fix them — we do not retain a parallel implementation as a hedge.

**Schema:**

```sql
CREATE TABLE IF NOT EXISTS jobs (
  id                   TEXT    PRIMARY KEY,
  product              TEXT    NOT NULL,
  status               TEXT    NOT NULL,         -- queued|running|completed|failed
  duration_seconds     REAL    NOT NULL,
  expected_exit_code   INTEGER NOT NULL,
  crash_at_pct         REAL,                     -- nullable
  slow_multiplier      REAL    NOT NULL DEFAULT 1.0,
  assigned_agent_id    TEXT,
  assigned_agent_epoch INTEGER,
  attempt_count        INTEGER NOT NULL DEFAULT 0,
  max_attempts         INTEGER NOT NULL DEFAULT 3,
  submitter            TEXT    NOT NULL,
  created_at           TEXT    NOT NULL,         -- ISO 8601
  started_at           TEXT,
  completed_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_product ON jobs(status, product);
CREATE INDEX IF NOT EXISTS idx_jobs_submitter      ON jobs(submitter);

CREATE TABLE IF NOT EXISTS job_events (
  job_id     TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  at         TEXT NOT NULL,
  kind       TEXT NOT NULL,
  agent_id   TEXT,
  agent_name TEXT,
  detail     TEXT,
  PRIMARY KEY (job_id, at, kind)
);
```

**Concurrency contract.** Writes serialize at the SQLite connection. The dispatcher's `asyncio.Lock` continues to be the *only* application-level mutex. No `SELECT ... FOR UPDATE`, no row-level locks. The DB is durable storage for state the dispatcher has already serialized.

**Library.** Stdlib `sqlite3` (sync). Discovered during implementation: the existing `JobStore` Protocol is sync, and SQLite operations are sub-millisecond local I/O — `aiosqlite` would force the Protocol async and cascade through the dispatcher for no real-world benefit. Single connection owned by the `SQLiteJobStore` instance. WAL mode enabled. Zero new dependencies.

**File location.** `./tss.db` by default. CLI flag `--db-path` overrides; `--db-path :memory:` for ephemeral.

**Mutation contract.** The existing dispatcher mutates `Job` objects in place after reading them from the store (the in-memory store works because Python returns references). For SQLite to persist those mutations, we add an explicit `update(job: Job) -> None` method to the `JobStore` Protocol. The dispatcher calls `store.update(job)` after each mutation site. This is the smallest possible change to the existing dispatcher logic — no behavior change, just an explicit write-back call.

### 7.2 Add `submitter` to `Job`

```python
class Job(BaseModel):
    ...existing fields...
    submitter: str  # required at submission time
```

- **CLI:** `tss submit-job` defaults `submitter` to `os.environ["USER"]`; `--submitter` overrides.
- **Web form:** reads from `localStorage["tss.submitter"]`; first visit prompts via a small inline banner.
- **Filter API:** `GET /api/jobs?submitter=<name>` — drives the *Mine* filter on the dashboard.

### 7.3 `/metrics` endpoint (Prometheus)

```
GET /metrics  →  Prometheus text exposition (Content-Type: text/plain; version=0.0.4)
```

| Metric | Type | Source |
|---|---|---|
| `tss_jobs_queued`                   | Gauge     | count of QUEUED jobs |
| `tss_jobs_running`                  | Gauge     | count of RUNNING jobs |
| `tss_jobs_completed_total`          | Counter   | increment on COMPLETED transition |
| `tss_jobs_failed_total`             | Counter   | increment on FAILED transition |
| `tss_reassignments_total{reason}`   | Counter   | increment on requeue (`reason ∈ agent_offline | overrun | agent_failure`) |
| `tss_agents_total{status}`          | Gauge     | counts by status |
| `tss_agent_heartbeat_age_seconds`   | Histogram | `now - last_heartbeat_mono`, sampled per scrape |

Library: `prometheus_client`. Computed on scrape from the dispatcher's snapshot — not on the hot path.

**No Grafana stack.** The endpoint exists so Section 4 of the talk says *"the wiring is in; here's what we'd scrape."*

### 7.4 API additions

| Endpoint | Behavior |
|---|---|
| `GET /api/jobs/{job_id}` *(new)* | Returns the full `Job` plus its `JobEvent` history. Source for the dashboard's job detail panel (§8.3). 404 if unknown. |
| `GET /api/jobs?submitter={name}` *(new query param on existing route)* | Filters the job list to a single submitter. Combinable with existing `status_filter` and `product` params. |
| `GET /metrics` *(new — see §7.3)* | Prometheus exposition. |

No other endpoints change. Existing routes (`/api/agents/*`, `/api/jobs`, `/api/fleet/status`, `/api/agents/{id}/jobs/next`, etc.) keep their contracts.

### 7.5 Tests migrate to `SQLiteJobStore(":memory:")`

```python
# tests/conftest.py — new fixture
@pytest.fixture
async def job_store():
    s = SQLiteJobStore(":memory:")
    await s.init()
    yield s
    await s.close()
```

Existing tests change `InMemoryJobStore()` → `job_store` fixture. Net: ~5–10 lines per test file.

The three race tests (`test_concurrent_claim`, `test_stale_agent`, `test_per_job_overrun`) **must pass against SQLite**. That's the regression bar. Same for the chaos test — `pytest -m chaos` clean against SQLite.

New tests:

- `tests/unit/test_sqlite_store.py` — direct exercise of the new implementation: create / get / update / query-by-capabilities / history append / persistence across reconnect.
- `tests/integration/test_submitter_filter.py` — `GET /api/jobs?submitter=alice` returns only alice's jobs.

## 8. Dashboard changes (existing `tss/server/static/`)

Three additive features. No HTML/CSS rewrite. No build step. JS file grows from ~290 LOC to a target of ≤500 LOC.

### 8.1 Identity prompt

On first dashboard visit (no `localStorage["tss.submitter"]`), a small banner appears at the top of the page:

```
┌──────────────────────────────────────────────────────────────────────┐
│  Tell us your name to filter jobs you submit:                        │
│  [_________________]   [Save]                                        │
└──────────────────────────────────────────────────────────────────────┘
```

After save, the banner is replaced by a small subtle indicator in the top bar (e.g. `as: jackson [change]`). Click *change* to update.

Stored in `localStorage["tss.submitter"]`. Drives the *Mine* filter and the completion toast.

### 8.2 *Mine* filter

A toggle in the top bar (or in a filter row above the queue/running tables):

```
[●] Mine only                           ← default ON if submitter set, OFF otherwise
```

When ON, the queue and running tables filter to jobs where `job.submitter === currentSubmitter`. The agent tiles do not filter — operators always see the whole fleet.

Implementation: client-side filter on the existing polling response. No new endpoint required.

### 8.3 Job detail panel

Click any job row (in queue, running, or recent events) → a side panel slides out from the right with:

- Header: job id, product, status badge, submitter
- **If RUNNING:** assigned agent, started-at, declared duration, attempt N of M
- **If terminal (COMPLETED / FAILED):** outcome (exit code, total elapsed, final error message if any)
- **History:** every `JobEvent` rendered as a vertical timeline — submitted, claimed, reassigned, etc.
- **Raw payload:** collapsible code block with the full JSON of the job

Implementation: ~150 lines of vanilla JS + ~80 lines of CSS. Single `<aside>` element shown/hidden via a CSS class. Data sourced from a new `GET /api/jobs/:job_id` endpoint that returns the full job + history (already implementable as a small wrapper around `JobStore.get`).

### 8.4 Completion toast (browser `Notification` API)

When the polling diff detects that one of *my* jobs (matching `localStorage["tss.submitter"]`) has transitioned to COMPLETED or FAILED, fire a browser notification:

```
┌────────────────────────────┐
│  TSS                       │
│  J7 completed in 8.2s     │
└────────────────────────────┘
```

- Permission requested on first identity save (after the user has shown intent).
- If permission denied or unsupported, fall back to a flash banner inside the dashboard.
- ~30 lines of JS total.

## 9. Testing strategy

| Layer | Approach | Bar |
|---|---|---|
| Backend unit | pytest fixture using `SQLiteJobStore(":memory:")` | All existing tests pass; coverage parity |
| Backend integration | Same fixture; race tests mandatory | `test_concurrent_claim`, `test_stale_agent`, `test_per_job_overrun` non-negotiable |
| Backend chaos | Run against SQLite. Debug and fix any SQLite-specific issues; do *not* fall back to a parallel store | `pytest -m chaos` clean |
| New: SQLite store CRUD | `tests/unit/test_sqlite_store.py` | Direct exercise of the new implementation |
| New: submitter filter | `tests/integration/test_submitter_filter.py` | `?submitter=` correctly filters |
| Dashboard smoke | Manual checklist (browser) before the demo | Identity prompt → save → Mine filter → submit → toast → click row → detail panel renders |

`mypy --strict` and `ruff` continue to gate Python.

## 10. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | SQLite locking flakes the chaos test | Medium | High | Debug and fix. Likely culprits: connection reuse across tasks, missing WAL mode, transaction scope. Don't paper over with a parallel implementation. |
| 2 | `aiosqlite` connection lifecycle bugs (e.g., closing during concurrent ops) | Low-Med | Medium | Single-connection-per-store invariant. Connection lifecycle tied to dispatcher app lifespan. |
| 3 | Pydantic serialization of `datetime` to ISO string round-trip diverges | Low | Medium | Single helper `_iso(dt)` / `_parse_iso(s)` in `SQLiteJobStore`; unit-test round-trip. |
| 4 | Browser `Notification` API permission denied / unsupported | Medium | Low | Fallback to in-page flash banner. |
| 5 | Identity prompt is annoying on every visit if localStorage clears | Low | Low | Quiet placement; not a modal. Easy to dismiss. |
| 6 | iCloud-style venv corruption | Low (we moved off `~/Desktop`) | High | Pre-flight `uv sync`. |
| 7 | Dashboard JS exceeds maintainability ceiling as features grow | Med (long-term) | Low (now) | Out-of-scope refactor. We accept a 500 LOC ceiling for this build; if it grows past that, that's the signal a framework is justified. |
| 8 | Audience asks *"why no UI framework?"* | Always | Low | We have an explicit answer in §5.3 and the README. |

## 11. Implementation milestones

Sequenced so each phase is independently shippable. Order is conservative — each phase's output is verifiable before the next begins.

| Phase | What ships | Stop-here? |
|---|---|---|
| **0. Spec + plan + branch** | This spec, implementation plan, working branch | n/a |
| **1. Backend foundations** | `SQLiteJobStore`, `submitter` field, `/metrics`, all tests migrated and green (including chaos) | Yes — backend story is fully shippable on its own |
| **2. Customer-facing dashboard additions** | Identity prompt, *Mine* filter, job detail panel, completion toast, smoke checklist passes | Yes — customer-impact bar reached |
| **3. Pre-demo dress rehearsal** | Manual smoke checklist run end-to-end on the demo machine; screenshot capture for slides | Required regardless |

## 12. Open questions

| Question | Default if unanswered | When we'd revisit |
|---|---|---|
| Where does the *Mine* filter toggle live — top bar vs above the tables? | Above the tables (closer to what it filters) | At implementation time, after eyeballing |
| Should the toast also fire for reassignments (not just terminal states)? | No — too noisy. Terminal states only. | If the customer story benefits |
| Should `/metrics` require auth? | No (honor system, internal network) | Production: yes |
| Is there a max number of jobs the dashboard renders before pagination? | Yes — last 200 by `created_at desc`. | If it gets noisy |
| Should the detail panel surface `JobEvent.detail` raw, or pretty-printed? | Raw. The raw payload section already shows everything else; the history rendering keeps `detail` as-is. | If readability suffers |

## 13. Mapping to the assessment rubric

| Brief requirement | Where this spec satisfies it |
|---|---|
| Pillar 1 — Registration & Capability | Existing `register` + capability matching (unchanged) |
| Pillar 2 — Intelligent Routing | Existing `claim_next_job` (unchanged) + `submitter` field exposes the customer's view of their own queue |
| Pillar 3 — Resiliency | Existing watchdog + epoch invariant + persisted `JobEvent` history (so reassignment is now *visible* to the customer in the detail panel, not just internal) |
| Pillar 4 — Visibility | Mine filter + job detail panel + completion toast — *visibility for the customer specifically* |
| Section 1 (Demo) | Phase 3 dress rehearsal + the dashboard additions |
| Section 2 (Customer Impact) | Personas section (§4) + customer-facing dashboard additions (§8) + `submitter` field |
| Section 3 (AI Partner Reflection) | The decision to *not* let AI scaffold a React rebuild is itself the talking point: AI defaults to "rewrite in a framework"; the engineer's job is to ask whether that delivers customer value vs polish |
| Section 4 (Scale & Evolution) | `JobStore` Protocol with the in-memory→SQLite swap as the demonstrable seam + `/metrics` endpoint as wiring; existing `docs/scale-evolution.md` for the talk |

---

**End of spec.** Pending user review per the brainstorming flow before proceeding to the implementation plan.
