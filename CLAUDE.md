# Claude / AI Agent Working Rules

This codebase is a Test Scheduling Service for Hardware-in-the-Loop (HIL) firmware testing. The dispatcher's correctness is graded — race conditions are rejected.

## Hard rules

1. **`tss/server/dispatcher.py` and `tss/server/watchdog.py` are correctness-critical.** Do not propose edits that have not been written to the test suite first. New behavior requires a new test that fails before the change.

2. **One lock, one critical section.** All mutations of `AgentRegistry` and `JobStore` go through a single `asyncio.Lock` held by `Dispatcher`. Do not introduce per-resource locks. Do not call `asyncio.sleep` while the lock is held.

3. **Epoch invariant.** Every agent has an integer `epoch` that increments on (re-)registration. Every job claim records `assigned_agent_epoch`. Any heartbeat or job-result with a stale epoch is rejected (410 / 409). Do not weaken this check.

4. **Time is `tss.common.time.now()`, not `time.time()`.** The wrapper exists so tests can fake it. `time.monotonic()` for durations, never wall-clock for timeout math.

5. **No silent retries.** When a job is reassigned, append a `JobEvent` to its history. The dashboard relies on this for visibility.

## Style

- Type-hint everything in `tss/`. `mypy --strict` must pass.
- Pydantic v2 for all wire-level models.
- Async functions are async all the way down — do not call sync HTTP libraries from async code.
- Comments only when the *why* is non-obvious. Code should explain *what*.

## Tests

- `pytest -m "not chaos"` for fast tests during development.
- `pytest -m chaos` runs the long chaos integration test before any merge to main.
- The three race tests (`test_concurrent_claim`, `test_stale_agent`, `test_per_job_overrun`) are non-negotiable.

## Assessment context (Samsara "Building with AI" exercise)

The original prompt for this project. Source PDF: `Building_with_AI_Interview_Exercise_-_Automation_Team_v2.pdf`.

### The challenge

Samsara firmware teams rely on HIL test agents ("Testbeds") to validate code. Hardware is messy: testbeds go offline, networks flicker, products require specific configurations. Build a Test Scheduling Service (TSS) that manages this. AI use is encouraged, but the candidate must understand and own the quality.

### Four core requirements (the "pillars")

1. **Registration & Capability** — Testbeds check in with the TSS and declare which products they support (e.g., Vehicle Gateway, Asset Gateway).
2. **Intelligent Routing** — Service accepts test jobs and assigns them to an available, compatible agent.
3. **Resiliency** — If an agent disconnects or fails mid-test, the dispatcher detects the heartbeat failure and re-queues the job for a different compatible agent.
4. **Visibility** — Simple interface (CLI, TUI, or API endpoint) showing fleet status: which testbeds are busy, which are offline, queue progress.

### Expected use of AI

- Scaffold service boilerplate and API structures.
- Generate a chaos simulator that spins up multiple mock agents with randomized failure profiles.
- Get to a quick proof-of-concept for demo.
- Write comprehensive unit and integration tests.

The brief explicitly calls out: **"AI often misses edge cases like race conditions in thread-locking or socket timeouts."** The candidate is responsible for auditing, refining, and owning final code quality. This is why `dispatcher.py` and `watchdog.py` are gated by hand-written race tests.

### Presentation (20 minutes) — required content

1. **Demo** — live walkthrough of TSS handling chaos (agents dropping, jobs reassigned). Show the system diagram and explain component interactions step by step.
2. **Customer Impact** — how does this improve a firmware engineer's daily life? What design decisions were customer-driven? What's the next step? How would you sell it?
3. **AI Partner Reflection** — where did AI help, where was it sub-optimal? Walk through your AI process: how you started, key prompts, how you iterated, how you knew when something was good enough, AI tooling you'd ship permanently in the repo, when AI should be in the loop vs used to build deterministic code.
4. **Scale & Evolution** — how would architecture change at 1,000 agents across multiple global offices?

### Deliverables

- Codebase (GitHub link or zip).
- System diagram (block diagram of service, agents, fleet visualization).
- README with run instructions for dispatcher and mock agents.
- AI Log (optional but encouraged): example prompts and how the candidate iterated on AI output. → see `docs/ai-log.md`.

### How this codebase maps to the brief

| Requirement | Where it lives |
|---|---|
| Pillar 1: Registration & Capability | `tss/server/dispatcher.py::register`, capability matching in `JobStore.find_queued_for_capabilities` |
| Pillar 2: Intelligent Routing | `dispatcher.py::claim_next_job` (atomic match-and-assign under the lock) |
| Pillar 3: Resiliency | `tss/server/watchdog.py` + `dispatcher.py::reap_stale_agents` (heartbeat timeout + per-job overrun) |
| Pillar 4: Visibility | Web dashboard at `localhost:8080`, CLI (`tss agents`, `tss jobs`), `/api/fleet/status` |
| Chaos simulator | `tss/agent/chaos.py` (silent_death, partition, job_crash, slow_exec) + `tss chaos` CLI |
| AI Log | `docs/ai-log.md` |
| Scale answer | `docs/scale-evolution.md` |
| Presentation script | `docs/presentation-notes.md` |
