# Samsara TSS — Test Scheduling Service

A Test Scheduling Service for Hardware-in-the-Loop (HIL) firmware validation. Manages a fleet of "testbeds" with automatic capability-aware routing, heartbeat-based resiliency, and a live web dashboard.

Built as the Samsara Automation Team's Build-with-AI assessment.

![dashboard](docs/dashboard-screenshot.png)

## What it does

The four pillars from the assessment:

1. **Registration & Capability** — Testbeds check in and declare which products they support (`vehicle_gateway`, `asset_gateway`).
2. **Intelligent Routing** — Jobs are submitted with a required product. The dispatcher atomically claims the next compatible job for an agent that polls.
3. **Resiliency** — A heartbeat watchdog detects testbeds that go silent (≥ 3 missed heartbeats) and re-queues their in-flight jobs to other compatible agents. Late results from a previously-offline agent are rejected via an epoch invariant.
4. **Visibility** — A live web dashboard at `http://localhost:8080/` plus a Rich-based CLI (`tss agents`, `tss jobs`).

## Architecture

```mermaid
flowchart TB
    subgraph operator [" "]
        CLI["CLI<br/>(operator)"]
        Dash["Web Dashboard"]
    end

    subgraph dispatcher ["TSS Dispatcher (FastAPI · single asyncio.Lock)"]
        direction LR
        Reg[("AgentRegistry")]
        Store[("JobStore")]
        WD["Watchdog<br/>(reaps stale agents)"]
    end

    subgraph fleet [" "]
        A1["Agent vg-01<br/>vehicle_gateway"]
        A2["Agent ag-01<br/>asset_gateway"]
        A3["Agent (chaos)<br/>silent death"]
    end

    CLI -- "POST /api/jobs" --> dispatcher
    Dash -- "GET /api/fleet/status (1s)" --> dispatcher
    A1 -- "register · heartbeat · claim · result" --> dispatcher
    A2 -- "claim job" --> dispatcher
    A3 -. "missed heartbeat" .-> dispatcher

    classDef agent fill:#b2f2bb,stroke:#22c55e
    classDef chaos fill:#ffd8a8,stroke:#f59e0b
    classDef internal fill:#c3fae8,stroke:#0F2944
    class A1,A2 agent
    class A3 chaos
    class Reg,Store,WD internal
```

## Call sequence — three scenarios

```mermaid
sequenceDiagram
    autonumber
    participant Op as Operator
    participant D as Dispatcher
    participant A as Agent A
    participant B as Agent B

    rect rgb(245, 248, 252)
    note over Op,B: 1. Happy path
    A->>D: POST /register {name, caps}
    D-->>A: agent_id, epoch=1
    Op->>D: POST /jobs (J)
    A->>D: GET /jobs/next
    D-->>A: assignment {job=J, epoch=1}
    note over A: runs job (sleep)
    A->>D: POST .../result {epoch=1, exit=0}
    D-->>A: 204 (J=COMPLETED)
    end

    rect rgb(252, 248, 240)
    note over Op,B: 2. Silent death → reassignment
    A->>D: claim job J2 (epoch=1)
    note over A: process killed (silent)
    note over D: watchdog tick: now - last_hb > 6s<br/>→ A=OFFLINE, J2=QUEUED
    B->>D: GET /jobs/next
    D-->>B: J2 (epoch=B's, attempt=2)
    B->>D: POST result exit=0
    D-->>B: 204 (COMPLETED)
    end

    rect rgb(252, 240, 240)
    note over Op,B: 3. Stale-result rejection (the race)
    note over A: A's network unblocks
    A->>D: POST result for J2 (epoch=1, stale)
    D-->>A: 409 Conflict (logged + dropped)
    A->>D: POST heartbeat (epoch=1)
    D-->>A: 410 Gone (must re-register)
    A->>D: POST /register
    D-->>A: agent_id, epoch=2
    end
```

A live Excalidraw version of both diagrams (with hand-drawn aesthetic) is in `docs/diagrams.md`.

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
tss submit-job --product vehicle_gateway --duration 8
tss submit-job --product asset_gateway --duration 12

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
    routes/      # /api/agents, /api/jobs, /api/fleet/status
    static/      # dashboard HTML/CSS/JS + Samsara logo assets
  agent/         # Mock agent runner + chaos profiles
  cli.py         # Typer CLI: serve, agent, chaos, submit-job, agents, jobs
tests/
  unit/          # dispatcher, registry, store, chaos profile sampling
  integration/   # full HTTP flow, capability matching, reassignment, stale agent,
                 #   concurrent claim, per-job overrun, chaos
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

| Method | Path                                            | Description |
|--------|-------------------------------------------------|-------------|
| POST   | `/api/agents/register`                          | Register or re-register; returns `{agent_id, epoch, ...}`. |
| POST   | `/api/agents/{id}/heartbeat`                    | 204 if accepted, 410 if pruned (must re-register). |
| POST   | `/api/agents/{id}/kill`                         | Demo-only: simulates immediate disconnect. |
| GET    | `/api/agents`                                   | List all agents. |
| GET    | `/api/agents/{id}/jobs/next`                    | Atomic claim. 200 + assignment, 204 if no compatible job. |
| POST   | `/api/agents/{id}/jobs/{job_id}/result`         | 204 if accepted, 409 if stale (epoch mismatch or wrong owner). |
| POST   | `/api/jobs`                                     | Submit a job. |
| GET    | `/api/jobs`                                     | List jobs (optional `status_filter`, `product`). |
| GET    | `/api/fleet/status`                             | Snapshot for the dashboard. |
| GET    | `/`                                             | Dashboard HTML. |

OpenAPI docs are auto-generated at `/docs`.

## Configuration

All tunables are env-vars (with defaults shown):

| Variable | Default | Meaning |
|---|---|---|
| `TSS_HOST` | `127.0.0.1` | Dispatcher bind host. |
| `TSS_PORT` | `8080` | Dispatcher bind port. |
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

Sketched in `docs/scale-evolution.md`. Short version: swap `InMemoryAgentRegistry` and `InMemoryJobStore` for Postgres + Redis, partition by capability/region using NATS or Kafka, and run stateless dispatcher replicas behind a load balancer with a Postgres-advisory-lock leader for the watchdog.
