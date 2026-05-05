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
