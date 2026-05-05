# Diagrams

The README contains Mermaid versions of these diagrams that render natively on GitHub. This file holds the alternate Excalidraw renders (hand-drawn aesthetic) used during the live presentation, plus notes on what each one is showing.

## System diagram

The control plane is the **TSS Dispatcher** — a single FastAPI process holding the `AgentRegistry`, `JobStore`, and `Watchdog` behind a single `asyncio.Lock`. There is one queue, one set of agents, and one critical section that mutates them. Agents poll over HTTP; the dispatcher does not push.

Color key:
- Blue = client side (operator CLI, web dashboard)
- Navy / teal = dispatcher internals
- Green = healthy agents
- Amber = chaotic / failing agents
- Red = failure paths (heartbeat missed, stale result)

## Call sequence — three scenarios

The sequence diagram walks through three scenarios on the same canvas:

1. **Happy path** — register, submit, claim, run, complete. The simplest case; everything else is a deviation from this.
2. **Silent death → reassignment** — agent A claims a job, then dies. The watchdog detects the missed heartbeat, marks A offline, and re-queues the job. Agent B claims and completes it. The job's `attempt_count` increments; if it hits `max_attempts`, the job is marked FAILED instead of re-queued.
3. **Stale-result rejection** — the race the assessment is graded on. After A is offline and J is reassigned to B, A's network unblocks and it tries to POST a result. Because `assigned_agent_epoch` on the job has moved forward, the dispatcher returns 409 Conflict and drops A's result. A's heartbeat returns 410 Gone, forcing it to re-register with a fresh epoch before it can do anything else.

The epoch invariant — `assigned_agent_epoch` recorded at claim time and checked at result time — is the linchpin of correctness here. Without it, A's late result could overwrite B's correct one.

## Editing the Excalidraw versions

The diagrams in this conversation were built via the Excalidraw MCP. To edit them in the browser:

1. Open <https://excalidraw.com/>.
2. Take a screenshot of the rendered diagram and paste it as a reference.
3. Recreate or annotate as needed for slides.

Or rebuild from the spec by re-running the create_view call from the build session.
