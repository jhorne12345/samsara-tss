# Samsara TSS — Test Scheduling Service

A Test Scheduling Service for Hardware-in-the-Loop (HIL) firmware validation. Manages a fleet of "testbeds" with automatic capability-aware routing, heartbeat-based resiliency, and a live web dashboard.

Built as the Samsara Automation Team's Build-with-AI assessment.

![dashboard](docs/dashboard-screenshot.png)

## What it does

1. **Registration & Capability** — Testbeds check in and declare which products they support (`vehicle_gateway`, `asset_gateway`).
2. **Intelligent Routing** — Jobs are submitted with a required product. The dispatcher atomically claims the next compatible job for an agent that polls.
3. **Resiliency** — A heartbeat watchdog detects testbeds that go silent (≥ 3 missed heartbeats) and re-queues their in-flight jobs to other compatible agents. Late results from a previously-offline agent are rejected via an epoch invariant.
4. **Persistence** — All jobs and their full event history survive dispatcher restarts. SQLite-backed store (stdlib `sqlite3`, WAL mode) with an explicit `update(job)` contract at every mutation site. `--db-path` flag and `TSS_DB_PATH` env var control the file; `:memory:` keeps tests fast.
5. **Customer Visibility** — Every job carries a `submitter` field. The dashboard shows an identity banner, a "Mine only" filter, a job-detail slide-in panel with full event history, and browser completion notifications. `/metrics` exposes fleet counts in Prometheus text format.

## Architecture (one-minute version)

Three roles, one box.

```mermaid
flowchart LR
    Op["Operator<br/>(CLI or browser)"]
    D["TSS Dispatcher<br/>FastAPI · localhost:8080"]
    A1["Test Rig 1<br/>vehicle_gateway"]
    A2["Test Rig 2<br/>asset_gateway"]
    A3["Test Rig 3<br/>both products"]

    Op -->|"submit jobs<br/>view dashboard"| D
    D -.->|"assignments"| A1
    D -.->|"assignments"| A2
    D -.->|"assignments"| A3
    A1 -->|"register · heartbeat<br/>poll · report result"| D
    A2 --> D
    A3 --> D
```

Three things to know up front:

- **Rigs poll, the dispatcher doesn't push.** Polling is robust to flaky HIL networks.
- **One Python process, one queue, one `asyncio.Lock` around all writes.** Per-resource locks would be more code and not measurably faster at this scale.
- **SQLite is the durable store.** Jobs and event history survive restarts. Tests use `:memory:` mode so there's no file I/O overhead.

The full story — tech stack, dispatcher internals, data model, sequence diagrams, job state machine, and where to look first — lives in **[`docs/architecture.md`](docs/architecture.md)**. The hand-drawn Excalidraw renders for the live demo are in `docs/diagrams.md`.

## Quick start

```bash
# 1. Install (uses uv; Python 3.11+ required)
make install

# 2. Run the demo (requires tmux)
make demo

# Or, without tmux:
make demo-plain
```

`make demo` opens a tmux session with the dispatcher, 5 mock agents (vg-01, vg-02, ag-01, ag-02, combo-01), and an operator REPL. The dashboard is at <http://localhost:8080/>.

To exercise it:

```bash
# In a separate terminal (or the operator pane):
tss submit-job --product vehicle_gateway --duration 8 --submitter you
tss submit-job --product asset_gateway --duration 12 --submitter you

# Watch the dashboard tiles change. Click "kill (demo)" on a tile to simulate
# an agent disconnect and watch reassignment in real time.

tss agents       # rich table of fleet status
tss jobs         # rich table of jobs
```

## Chaos demo

The mock agent supports four failure modes via a chaos profile:

- `silent_death` — agent stops sending heartbeats entirely.
- `partition` — agent skips heartbeats for a stretch (network partition).
- `job_crash` — agent reports a job as failed at a random progress point.
- `slow_exec` — agent overruns the declared duration (caught by the per-job overrun watchdog).

```bash
# Spawn 10 agents with a mix of profiles
tss chaos --count 10 --intensity mixed

# Then submit 30 jobs and watch the dashboard
for i in $(seq 1 15); do tss submit-job --product vehicle_gateway --duration 8; done
for i in $(seq 1 15); do tss submit-job --product asset_gateway --duration 8; done
```

Every job reaches a terminal state, even with agents dying mid-run. This is verified by the chaos integration test (`pytest -m chaos`).

## Project layout

```
tss/
  common/        # Pydantic models, constants, fake-clockable time wrapper
  server/        # FastAPI app, dispatcher (single asyncio.Lock), watchdog
    routes/      # /api/agents, /api/jobs, /api/fleet/status, /metrics
    static/      # dashboard HTML/CSS/JS + Samsara logo assets
    sqlite_store.py   # SQLiteJobStore — the canonical job store
    store.py          # JobStore protocol
  agent/         # Mock agent runner + chaos profiles
  cli.py         # Typer CLI: serve, agent, chaos, submit-job, agents, jobs
tests/
  unit/          # dispatcher, registry, sqlite_store, chaos profile sampling
  integration/   # full HTTP flow, capability matching, reassignment, stale agent,
                 #   concurrent claim, per-job overrun, submitter filter,
                 #   job detail, metrics, chaos
```

The critical correctness lives in `tss/server/dispatcher.py` (one `asyncio.Lock`, all mutations behind it) and `tss/server/watchdog.py` (the async loop that calls `reap_stale_agents`).

## Testing

```bash
make test            # everything including the long chaos test (~5s)
make test-fast       # skip the chaos test
make test-chaos      # only the chaos test
make lint            # ruff
make typecheck       # mypy --strict
```

The three race-condition tests are non-negotiable:

- `tests/integration/test_concurrent_claim.py` — N agents poll simultaneously, exactly one wins.
- `tests/integration/test_stale_agent.py` — late result from an offlined agent returns 409, doesn't clobber the new agent's result.
- `tests/integration/test_per_job_overrun.py` — an agent that heartbeats but never finishes is force-requeued.

## API reference

OpenAPI docs are auto-generated at `/docs` (Swagger UI) and `/redoc`.

### Agents

**`POST /api/agents/register`** — Register or re-register a testbed. Safe to call repeatedly; increments the agent's epoch on each call so stale in-flight results from a previous incarnation are rejected.

```jsonc
// request
{ "name": "vg-01", "capabilities": ["vehicle_gateway"] }

// response 200
{ "agent_id": "<uuid>", "epoch": 1, "heartbeat_interval_s": 2.0, "poll_interval_s": 1.0 }
```

**`POST /api/agents/{agent_id}/heartbeat`** — Keep-alive from an agent. Must echo the current `epoch`.

```jsonc
// request
{ "epoch": 1 }
// 204 accepted  |  410 Gone → agent was pruned, must re-register
```

**`GET /api/agents/{agent_id}/jobs/next`** — Atomic job claim. The dispatcher matches the agent's capabilities against the queued jobs and assigns the oldest compatible one in a single lock step.

```jsonc
// 200 — job assigned
{
  "job_id": "<uuid>", "product": "vehicle_gateway",
  "duration_seconds": 8.0, "expected_exit_code": 0,
  "crash_at_pct": null, "slow_multiplier": 1.0, "epoch": 1
}
// 204 — no compatible job waiting
// 409 — agent is already running a job
// 410 — agent unknown, must re-register
```

**`POST /api/agents/{agent_id}/jobs/{job_id}/result`** — Report a job outcome. The `epoch` must match what was in the assignment; a mismatch means the agent was considered offline and a new agent already owns the job.

```jsonc
// request
{ "epoch": 1, "exit_code": 0, "duration_actual": 7.4, "error_message": null }
// 204 accepted  |  409 stale/wrong owner  |  410 unknown agent  |  404 unknown job
```

**`GET /api/agents`** — List all registered agents with their current status (`idle`, `busy`, `offline`).

**`POST /api/agents/{agent_id}/kill`** — Demo only. Simulates an immediate disconnect so you can watch job reassignment on the dashboard. Returns 204.

---

### Jobs

**`POST /api/jobs`** — Submit a test job. Only `product` and `duration_seconds` are required.

```jsonc
// request
{
  "product": "vehicle_gateway",   // required — must match an agent capability
  "duration_seconds": 8.0,        // required
  "submitter": "alice",           // optional, defaults to "unknown"
  "expected_exit_code": 0,        // optional
  "max_attempts": 3               // optional, 1–10; how many times to retry on agent failure
}

// response 201
{ "job_id": "<uuid>" }
```

**`GET /api/jobs`** — List all jobs. Supports query-string filters:

| Parameter | Values | Effect |
|---|---|---|
| `status_filter` | `queued`, `running`, `completed`, `failed` | Return only jobs in this state |
| `product` | any string | Return only jobs for this product |
| `submitter` | any string | Return only jobs from this submitter |

**`GET /api/jobs/{job_id}`** — Full job record including `history` (the ordered list of `JobEvent` objects that records every state transition: submitted, claimed, reassigned, completed, etc.).

---

### Fleet and metrics

**`GET /api/fleet/status`** — Dashboard snapshot: full agent list, queue, running jobs, recent events across all jobs, and aggregate counts.

**`GET /metrics`** — Prometheus text format. Exposes `tss_agents_total{status}` and `tss_jobs_total{status}` gauges. No extra dependencies required.

**`GET /`** — Live dashboard HTML.

## Configuration

All tunables are env-vars (with defaults shown):

| Variable | Default | Meaning |
|---|---|---|
| `TSS_HOST` | `127.0.0.1` | Dispatcher bind host. |
| `TSS_PORT` | `8080` | Dispatcher bind port. |
| `TSS_DB_PATH` | `./tss.db` | SQLite database path. Pass `:memory:` for an ephemeral in-memory instance. Overridden by `--db-path` CLI flag. |
| `TSS_HEARTBEAT_INTERVAL_S` | `2.0` | Agent heartbeat cadence. |
| `TSS_HEARTBEAT_TIMEOUT_S` | `6.0` | Mark agent OFFLINE after this many seconds without a heartbeat. |
| `TSS_WATCHDOG_INTERVAL_S` | `1.0` | How often the watchdog scans the registry. |
| `TSS_POLL_INTERVAL_S` | `1.0` | Agent's job-poll cadence. |
| `TSS_JOB_MAX_ATTEMPTS` | `3` | Default reassignment budget per job. |
| `TSS_MAX_OVERRUN_FACTOR` | `3.0` | A running job exceeding `duration × this` is force-requeued. |

## How AI was used

See `docs/ai-log.md` for the prompts and the places the LLM's first draft missed something. Short version:

* AI scaffolded models, FastAPI routes, dashboard HTML/CSS, CLI structure, and the first cut of every test.
* Hand-written: the lock semantics in `dispatcher.py`, the epoch invariant, the per-job overrun branch in the watchdog. These are the parts the assessment grades on, and an LLM's first draft missed each one.
* The dispatcher state machine is deterministic; the test suite is the source of truth, not the prompt.

## Scaling beyond 10 agents

Sketched in `docs/scale-evolution.md`. The SQLite store is already step 1. The remaining path: swap `InMemoryAgentRegistry` and `SQLiteJobStore` for Postgres + Redis, partition by capability/region using NATS or Kafka, and run stateless dispatcher replicas behind a load balancer with a Postgres-advisory-lock leader for the watchdog. The `JobStore` Protocol is the seam — no route or agent changes required.
