# AI Log — How AI was used in this build

This is the candid log requested by the assessment ("AI Log: a few examples of prompts you used to build the system and how you iterated on the AI's output"). It captures the prompts that mattered, the places the LLM's first draft was wrong, and the rules I'd add to a long-lived repo to keep AI-generated code honest.

## How I started

I did not start by asking an LLM to "build me a TSS." I started by having Claude **brainstorm with me** before writing any code:

1. What's the time budget and target polish? *(15 hours, "knock it out of the park" presentation.)*
2. Language / runtime — Python vs Go vs TypeScript? *(Python: fastest scaffold, lets me spend complexity budget on the chaos demo and presentation rather than fighting boilerplate.)*
3. Transport — HTTP polling vs WebSockets? *(HTTP polling with explicit heartbeats: matches "heartbeat" terminology in the assessment, robust to flaky HIL networks.)*
4. Persistence — in-memory vs SQLite vs Postgres? *(In-memory behind a `JobStore` / `AgentRegistry` interface. The seam is the answer to "how would you scale this.")*
5. Visibility — CLI, TUI, web? *(CLI + lightweight web dashboard. CLI for operator commands during demo, dashboard for screen-share visualization.)*
6. Job model — sleep simulation vs shell commands? *(Structured fake jobs: deterministic, no shell-quoting bugs, lets me craft demo scenarios.)*
7. Chaos profiles — which failure modes? *(Silent death, network partition, job crash, slow execution. Plus the per-job overrun edge case the assessment hints at.)*

That conversation produced the implementation plan in `/Users/jackson/.claude/plans/i-am-working-on-velvety-cupcake.md` (also committed). Every subsequent prompt referred back to it.

The discipline: **brainstorm before scaffolding**. AI is excellent at generating code that works for the wrong problem. Sorting out which problem to solve is the part you can't delegate.

## Key prompt categories

These are the prompt shapes that came up over and over:

### 1. "Scaffold X with these constraints"

```
"Write tss/server/dispatcher.py. Requirements:
 - One asyncio.Lock guarding all mutations of registry and store.
 - register/heartbeat/claim/report_result/submit_job operations.
 - epoch increments on re-registration; results checked against
   (assigned_agent_id, assigned_agent_epoch).
 - Stale results raise typed exceptions, not return None.
 - No shell or DB calls. In-memory only."
```

This produces something testable in one pass. AI is good at this.

### 2. "Generate a failing test for case Y"

```
"Write a pytest async test that demonstrates the stale-result race:
 Agent A claims job J at epoch 1. The watchdog marks A offline. Agent B
 (after re-registering with bumped epoch) claims J. Then A's late result
 arrives at epoch 1. Assert the dispatcher returns 409 and J is still
 RUNNING for B."
```

This is the high-leverage use of AI: encode the spec as an executable test, then write the dispatcher to make it pass. The tests in `tests/integration/` came from prompts of this shape.

### 3. "Review this critical section for race conditions"

```
"Review tss/server/dispatcher.py:claim_next_job. Are there any sequences
 of concurrent calls that could result in:
   (a) two agents claiming the same job
   (b) an agent claiming while OFFLINE
   (c) the JobStore mutating without the lock
 Identify by code path; don't suggest fixes."
```

Useful for catching obvious mistakes. Less useful for subtle ones — see the next section.

### 4. "Generate test fixtures and chaos profiles"

Naturally probabilistic things like chaos profile distributions are great for AI. There is no "wrong answer" — only "less interesting demo." I sampled a few profile sets and picked the ones that produced the most varied chaos test runs.

## Where AI was sub-optimal (real instances from this build)

These are concrete cases where the LLM's first draft was wrong, with the lesson:

### 1. Wall-clock vs monotonic clock for timeouts

**First draft:** `if time.time() - agent.last_heartbeat > HEARTBEAT_TIMEOUT_S:`

**Problem:** Wall clock can jump forward (NTP correction) or backward (DST, manual change). Heartbeat math must use a monotonic clock.

**Fix:** Introduced `tss/common/clock.py` with `monotonic()` and `utcnow()` accessors, and used them everywhere. The added benefit: tests can install a fake clock without monkey-patching `time.time`, so the dispatcher's production code path is exercised verbatim in tests.

**Lesson:** AI defaults to the obvious-but-wrong API. Time, locking, and concurrency primitives need a code review every single time.

### 2. Optimistic update with no lock

**First draft:** `claim_next_job` did `self._store.find_queued_for_capabilities(...)` then `job.status = RUNNING` without a lock. Two simultaneous calls could both find and assign the same job.

**Fix:** Wrapped the entire match-and-assign in `async with self._lock:`. Wrote `tests/integration/test_concurrent_claim.py` to assert that two simultaneous claims for one queued job produce exactly one winner. Test failed before fix, passed after.

**Lesson:** "AI misses race conditions in thread-locking" — *the assessment literally said this*. Always assume an AI-generated critical section needs a hand-written lock.

### 3. Stale-result rejection didn't exist

**First draft:** `report_result` checked that the agent and job existed, then updated the job. No epoch check.

**Problem:** The classic race — agent A is marked offline, job J is reassigned to agent B, A's network unblocks, A's late result arrives and clobbers B's correct one.

**Fix:** Introduced an `epoch` field on `Agent`, recorded `assigned_agent_epoch` on the `Job` at claim time, and added the three-way check (caller's epoch == agent's current epoch == job's recorded epoch and `assigned_agent_id == caller`). Wrote `tests/integration/test_stale_agent.py` to lock this in.

**Lesson:** AI generates the *common* path correctly. Distributed-systems edge cases (zombie agents, late results, fencing tokens) need to be designed in, not bolted on.

### 4. Per-job overrun detection was missing entirely

**First draft:** The watchdog only checked heartbeat freshness. An agent that heartbeated forever but never finished its job was invisible.

**Fix:** Added a second branch to `reap_stale_agents` that compares `now - job.started_at` to `duration_seconds * MAX_OVERRUN_FACTOR` and force-requeues the job, freeing the agent. `tests/integration/test_per_job_overrun.py` is the regression.

**Lesson:** AI scaffolds the obvious watchdog behavior. The "stuck-but-alive" case is exactly the kind of thing that bites in production — the assessment specifically calls it out as something AI misses.

### 5. The ASGI test client returned the wrong status for `204 No Content`

Not really an AI bug — but I caught the LLM happy to write `return None` from an endpoint that needs an explicit `Response(status_code=204)`. FastAPI infers the status from the return value, and `None` becomes `200` with `null` body. The fix: explicit `Response(status_code=status.HTTP_204_NO_CONTENT)` in `routes/agents.py` and `routes/jobs.py`.

## How I knew when something was "good enough"

The assessment asks this directly. My rule, by component:

- **Dispatcher / watchdog** (correctness-critical): all unit + integration tests pass, the three race tests pass (`test_concurrent_claim`, `test_stale_agent`, `test_per_job_overrun`), and `pytest -m chaos` runs clean with reassign-count > 0. If any of those fails, "good enough" is not yet hit, no matter how clean the code looks.
- **CLI / dashboard** (presentation-critical): a 5-minute live demo dry-run must be readable on screen-share without commentary. If a viewer can't tell what's happening from the dashboard alone, it's not done.
- **Scaffolded code I didn't hand-write** (routes, Pydantic models, dashboard CSS): I read every line before commit and ask "would I have written it this way?" If the answer is "no but it works," I rewrite.

## Permanent AI tooling I'd ship in this repo

- `pre-commit` with `ruff format`, `ruff check`, `mypy --strict`. AI-generated code is decent on style but lazy on types — the hook catches both before review.
- `pytest --hypothesis-show-statistics` in CI for property-based fuzzing of the dispatcher state machine. LLM-generated unit tests cover the example you gave them; hypothesis covers the cases neither of you thought of.
- A `CLAUDE.md` / `AGENTS.md` at repo root encoding the lock discipline and "AI may not edit dispatcher.py or watchdog.py without a passing race-condition test" rule. (Already committed.)
- Optional: a CI-gated AI code review on PRs — useful for catching obvious regressions in test coverage and lock semantics, but only as a *suggester*, never as a gate.

## When AI in the loop vs deterministic

**Deterministic** (LLM helps me author, but the running artifact is plain code under test): everything correctness-critical. The dispatcher state machine, lock semantics, epoch invariants, capability matching. The artifact ships as code, the tests are the source of truth, and the LLM is irrelevant after merge.

**AI in the loop** (LLM is a runtime component): would only fit places where natural-language input or fuzzy classification matters — e.g. "given a free-text bug report from CI, route it to the right team," "given a flaky test pattern, suggest the likely culprit." None of that exists in TSS today.

**The line:** if I can write a property-based test that verifies the behavior, it should be deterministic code. If the input space is open and human-shaped, AI in the loop earns its place. The TSS dispatcher fails the first half of that test conclusively, so it stays deterministic.

## Total AI usage shape

Rough breakdown by line count (estimate):

- ~70% scaffolded by AI (Pydantic models, FastAPI routes, dashboard HTML/CSS, CLI structure, agent runner skeleton, first-draft tests).
- ~20% AI-scaffolded then hand-rewritten (dispatcher core, watchdog branches, race-condition tests).
- ~10% written from scratch by hand (lock discipline, epoch invariant, the three race tests).

The AI partnership shape that worked: **let it scaffold, then audit every line that touches concurrency, time, or money.** For TSS there's no money — but every concurrency line was hand-checked.
