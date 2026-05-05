# Presentation Notes (20 minutes)

Private cheat sheet. Times are guidance, not gospel — the demo can run long if Q&A engages.

## Slide order

1. **Title + problem** (1 min)  
   - "HIL is messy: testbeds drop, networks flicker, products require specific hardware."
   - "We built TSS — Test Scheduling Service — to absorb that mess."

2. **System diagram** (2 min)  
   - Walk through the four pillars, point to where each lives in the diagram.
   - Emphasize: one dispatcher process, one lock, in-memory + interface seam for scale.

3. **Live demo** (5 min) — see "Demo script" below.

4. **Resiliency deep-dive** (3 min)  
   - The sequence diagram: register → claim → die → reassign → stale rejection.
   - Epoch invariant is the key insight.
   - "AI's first draft of `report_result` did not have this. The race only shows up under concurrency. The test caught it."

5. **Customer impact** (2 min)  
   - Firmware engineer's day, before/after.
   - Selling pitch: "How many hours/week does your team lose chasing flakes? Multiply by team size."

6. **AI partner reflection** (3 min)  
   - 3 wins (scaffold, tests, dashboard).
   - 3 catches (clock, lock, epoch).
   - "Deterministic vs in-the-loop" rule.

7. **Scale: 10 → 1,000** (2 min)  
   - Postgres + Redis + NATS + regional sharding.
   - "The interfaces I drew today are the seams I'd use tomorrow."

8. **Q&A** (2 min)

## Demo script (5 minutes, with `make demo` already running)

1. Open the dashboard: `http://localhost:8080/`. Point out: 5 agents idle (vg-01, vg-02, ag-01, ag-02, combo-01), queue empty.
2. In operator pane:
   ```
   tss submit-job --product vehicle_gateway --duration 8
   tss submit-job --product asset_gateway --duration 12
   tss submit-job --product vehicle_gateway --duration 6
   tss submit-job --product asset_gateway --duration 10
   ```
3. Point at dashboard: 4 tiles go yellow (busy). Queue 0, running 4. Capability matching just worked invisibly — the VG jobs went to VG agents, etc.
4. **Click "kill (demo)" on vg-01.** Tile goes red. Watchdog detects within ~6s. Job's attempt counter goes 1→2 in the queue panel; vg-02 picks it up.
5. **Submit a "doomed" job:** `tss submit-job --product vehicle_gateway --duration 6 --crash-at 0.5`. Watch it claim, fail at 50%, requeue with attempt 2, claim again, fail again, finally FAILED at attempt 3.
6. **Submit unmatched:** `tss submit-job --product unsupported_product --duration 5`. It sits in the queue forever — no compatible agent. (This is an explicit feature, not a bug — would be a dashboard warning in v2.)
7. **CLI views:** `tss agents`, `tss jobs --status failed`. Rich tables for the operator who lives in the terminal.
8. Bring back vg-01: in its tmux pane, just `tss agent --name vg-01 --caps vehicle_gateway`. It re-registers with epoch=2, joins idle, picks up the next job.

## Talking points by question

### "Why polling instead of WebSockets?"
Two reasons: HIL testbeds are behind office firewalls and flaky networks; reconnection state machines are exactly where AI gets things wrong. Polling handles the "agent vanished for 30 seconds and came back" case trivially. Heartbeat semantics are explicit, not implicit-from-TCP.

### "Why one lock instead of per-resource locks?"
The lock is held only for the duration of a state mutation — microseconds. Lock contention is invisible at this fleet size. Per-resource locking introduces lock-order risk for no measured benefit. If the lock ever shows up in a profile, the answer is "switch to Postgres," not "split the lock."

### "What's the epoch field for?"
Stale-result rejection. When an agent is marked offline and re-registers, its epoch increments. Late results from the old incarnation carry the old epoch and are rejected with 409. Without this, agent A's late result could clobber agent B's correct one for the same job.

### "Why no SQLite for durability?"
The dispatcher restarts wipe state today. Acceptable for a demo. The `JobStore` interface is the seam — we'd add a Postgres backend in a day, well before adding SQLite. SQLite would be 3 hours of work for a story that doesn't strengthen the demo or the resiliency narrative.

### "Why in-memory at all?"
Because the assessment is a 15-hour build of a proof-of-concept and the time is best spent on the chaos demo, the test suite, and the AI partner narrative. The interface seam means scale is a swap, not a rewrite.

### "Where did AI hurt you?"
Three real cases (in `docs/ai-log.md`): wall-clock instead of monotonic for timeouts; optimistic-update without a lock in `claim_next_job`; no epoch check in `report_result`. Each was caught by writing a test for the behavior I expected, then watching it fail.

### "How would you ship this to production?"
Order in `docs/scale-evolution.md`: Postgres backend, Redis heartbeats, stateless dispatcher with leader election for the watchdog, NATS for wake-ups, regional sharding, observability, mTLS, priority queues. Roughly 4 weeks for the first cut.

## Backup plan

If `make demo` fails on stage: pre-recorded 3-minute screen capture of the exact same demo script. Have it in a browser tab labeled "demo backup" before starting.

If the chaos test fails on stage: it's flaky enough that one rerun usually fixes it. If it fails twice, point at `pytest -m "not chaos"` (the deterministic 35 tests) which is bulletproof.
