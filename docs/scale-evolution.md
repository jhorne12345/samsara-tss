# Scale Evolution — 10 → 1,000 Agents Across Global Offices

The assessment asks: "How would your architecture change if we scaled from 10 agents to 1,000 across multiple global offices?" Here is the answer.

## What stays the same

The four pillars and the agent-side contract:

- Agent registration with declared capabilities.
- Heartbeat-based liveness (interval and timeout become tunable per region rather than constants).
- Capability-aware atomic claim.
- Epoch-based stale-result rejection.

The clean `JobStore` / `AgentRegistry` interfaces in `tss/server/` are the seam where local becomes distributed. No agent code changes.

## What changes

### 1. Storage: in-memory → durable

| Today | At 1,000 agents |
|---|---|
| `InMemoryAgentRegistry` (Python dict) | Redis hash, TTL on `last_heartbeat_mono`. Heartbeat = `EXPIRE`, watchdog = `KEYS` + `TTL` checks. |
| `SQLiteJobStore` (single-file SQLite, WAL) | Postgres table `jobs(id PK, product, status, assigned_agent_id, assigned_agent_epoch, attempt_count, submitter, ...)`. The atomic claim becomes `UPDATE ... WHERE status='queued' AND product = ANY(:caps) RETURNING ... LIMIT 1` — Postgres' row-level locking is the single source of truth that two agents can't both claim. |
| `job_events` table (SQLite, FK) | Same schema in Postgres; the `JobStore` Protocol swap requires no changes to routes, dispatcher, or agents. |

The clean interfaces mean the swap is module-local: no route changes, no agent changes.

### 2. Dispatcher: single process → stateless replicas

- Run N stateless dispatcher pods behind a Layer 7 load balancer.
- The `asyncio.Lock` was the in-memory critical section; with Postgres, the row-level lock from `SELECT ... FOR UPDATE SKIP LOCKED` replaces it. No application-level mutex needed for the claim path.
- Watchdog needs **leader election** so only one replica reaps stale agents at a time. Implementation: Postgres advisory lock (`pg_try_advisory_lock(WATCHDOG_KEY)`), held for the watchdog's lifetime. If the lock-holding pod dies, another pod picks it up within seconds.
- Heartbeat writes go to Redis (not Postgres) — the write rate from 1,000 agents at 2-second cadence is 500 writes/sec, which is trivial for Redis but loud on Postgres.

### 3. Job dispatch: agents poll → message queue

At 10 agents, polling at 1Hz costs 10 GET/s. Negligible.

At 1,000 agents across multiple regions, polling at 1Hz costs 1,000 GET/s on the dispatcher. Doable but wasteful — most polls return 204.

Replace polling with a message queue:

- **NATS JetStream** or **Kafka**, with one subject/topic per `(product, region)` pair. e.g. `jobs.us-west.vehicle_gateway`, `jobs.eu-central.asset_gateway`.
- Agents subscribe to the subjects matching their capabilities + region.
- Job submission writes to Postgres (durable) AND publishes a "new job available" notification on the relevant subject.
- Atomic claim still goes through the dispatcher (Postgres row lock); the queue is just the wake-up signal.

Trade-off: more moving parts. Worth it at 1,000 agents; not worth it at 10.

### 4. Regional sharding

Don't try to run one global TSS. Run **one TSS per region** (us-west, us-east, eu-central, ap-southeast).

- Each agent registers with its nearest dispatcher.
- Most jobs run within-region; the agent-to-dispatcher latency stays under 50ms.
- Cross-region failover only when a product is exclusively available in another region, and only via an explicit `cross_region_ok=true` flag on the job submission.
- A **global control plane** aggregates fleet status from all regions for a "fleet of fleets" dashboard, and exposes a single submit-job API that picks a region based on declared product availability.

Operations stays sane: each region has its own oncall, its own metrics, its own SLOs. A failure in eu-central doesn't page us-west.

### 5. Observability

What we have today: structured logs and the dashboard.

What we'd add at scale:

- **OpenTelemetry traces** from `submit-job` through `claim` through `result`. Spans across the dispatcher process. Baggage propagation for `agent_id` and `job_id` so the trace ties together across services.
- **Prometheus metrics** on each dispatcher: `tss_jobs_queued`, `tss_jobs_running`, `tss_jobs_completed_total`, `tss_jobs_failed_total`, `tss_agent_heartbeat_age_seconds` histogram, `tss_reassignment_total{reason}` counter.
- **Per-agent SLOs**: heartbeat-success-rate (% of expected heartbeats that arrived on time, week-over-week) and job-success-rate (% of claimed jobs that completed without reassignment). Agents with persistently low SLOs get auto-paged for hardware investigation.
- **Per-product latency**: time from job-submitted to first claim. If this grows, the product is capacity-constrained — surface in the dashboard with a "demand vs supply" heatmap.

### 6. Agent isolation

At 1,000 agents, blast radius matters. A misbehaving agent today can re-register with the same name and clobber state. At scale, add:

- An **agent identity certificate** (mTLS) issued at provisioning time. The dispatcher verifies the cert and binds it to the agent record. Re-registration with the same name from a different cert is rejected.
- **Per-agent rate limits** on the dispatcher (heartbeat / claim / result), so a stuck agent loop can't DoS the dispatcher.
- **Quarantine**: an agent with N consecutive job failures is auto-quarantined (taken out of the claim pool) and pages oncall for inspection. Today this is done manually; at scale it has to be automatic.

### 7. Job priority and fairness

Today: FIFO queue, no priorities. Fine for 10 agents.

At scale: tenants (firmware teams) compete for the same fleet. Add:

- **Priority queues** with weighted-fair-queue semantics: each team gets a guaranteed slice of fleet capacity.
- **Per-team quotas** and dashboards.
- **Job tagging**: `release_blocker=true` jobs jump the queue; nightly regression jobs deprioritize when interactive jobs are waiting.

This is policy-heavy and customer-driven — no point building it before there are customers asking for it.

## Order of operations

If you handed me this same problem at 1,000 agents on day 1, I'd build it in this order:

1. ~~SQLite `JobStore`~~ ✅ Already done — single-file SQLite with WAL mode, full event history, survives restarts.
2. Postgres `JobStore` + `SELECT FOR UPDATE SKIP LOCKED` claim. (Days 1-3)
2. Redis `AgentRegistry` for heartbeats. (Day 4)
3. Stateless dispatcher replicas + Postgres-advisory-lock watchdog leader. (Days 5-6)
4. NATS-based wake-ups (replace polling). (Days 7-9)
5. Regional sharding + global control plane. (Days 10-15)
6. Observability (OTel + Prometheus). (Days 16-18)
7. mTLS, rate limits, quarantine. (Days 19-21)
8. Priority queues. (Only after a real customer demand for them.)

The whole exercise is roughly four weeks. The agent-side contract doesn't change once.

## Why the proof-of-concept design holds up

The fact that the in-memory dispatcher passes the same tests an SQL-backed one would (atomic claim, epoch invariant, capability matching) is the answer. The interfaces I drew today are the seams I'd use tomorrow. The complexity I avoided in this build is the complexity I'd add deliberately in production, in the order above, driven by actual capacity and reliability problems — not by speculation.
