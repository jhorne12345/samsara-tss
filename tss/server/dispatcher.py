"""The Test Scheduling Service dispatcher.

Holds all server-side state behind a single asyncio.Lock and exposes the
operations the HTTP routes and watchdog call. **All** mutations to the
registry and store go through this class; do not bypass.

Design notes:

* One lock, one critical section. Two agents claiming concurrently must not
  both win the same job; the lock makes the queue scan + assignment atomic.
* The watchdog acquires the same lock as job claims. There is no separate
  watchdog mutex, so there cannot be a lock-order inversion.
* ``report_result`` checks the agent's epoch *and* that the job is still
  assigned to that specific (agent, epoch) tuple. A late result from a
  zombie agent is silently dropped (with a logged event).
* No ``await`` other than ``self._lock`` is allowed inside the critical
  sections — keep them short, deterministic, and side-effect-free.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import Literal
from uuid import UUID

from tss.common import clock
from tss.common.constants import (
    HEARTBEAT_INTERVAL_S,
    HEARTBEAT_TIMEOUT_S,
    JOB_MAX_ATTEMPTS,
    MAX_OVERRUN_FACTOR,
    POLL_INTERVAL_S,
)
from tss.common.models import (
    Agent,
    AgentStatus,
    EpochSummary,
    FleetStats,
    FleetStatusResponse,
    Job,
    JobAssignment,
    JobEvent,
    JobStatus,
)
from tss.server.errors import (
    AgentNotIdleError,
    JobNotAssignedToAgentError,
    StaleEpochError,
    UnknownAgentError,
    UnknownJobError,
)
from tss.server.registry import AgentRegistry, InMemoryAgentRegistry
from tss.server.sqlite_store import SQLiteJobStore
from tss.server.store import JobStore

log = logging.getLogger(__name__)

THROUGHPUT_BUCKETS: int = 12
"""Number of one-minute buckets in the throughput sparkline series."""


class Dispatcher:
    """Owns the registry, store, and the single mutex that serializes all writes."""

    def __init__(
        self,
        *,
        registry: AgentRegistry | None = None,
        store: JobStore | None = None,
        heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S,
        heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S,
        poll_interval_s: float = POLL_INTERVAL_S,
        max_overrun_factor: float = MAX_OVERRUN_FACTOR,
        default_max_attempts: int = JOB_MAX_ATTEMPTS,
    ) -> None:
        self.registry: AgentRegistry = registry or InMemoryAgentRegistry()
        self.store: JobStore = store if store is not None else SQLiteJobStore(":memory:")
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.heartbeat_interval_s = heartbeat_interval_s
        self.poll_interval_s = poll_interval_s
        self.max_overrun_factor = max_overrun_factor
        self.default_max_attempts = default_max_attempts
        self._lock = asyncio.Lock()
        # Monotonic timestamps of recent job completions (success *or* failure).
        # Drives the throughput sparkline. Capped to keep memory bounded — at
        # ~10 jobs/sec the cap covers ~3.4 minutes, which is plenty for a
        # 12-bucket per-minute series.
        self._completion_times: deque[float] = deque(maxlen=2048)

    # ----- Registration & heartbeat -----

    async def register(self, name: str, capabilities: list[str]) -> Agent:
        """Register a new agent, or re-register an existing one with a bumped epoch.

        Re-registration is how an agent recovers from being marked OFFLINE.
        The new epoch invalidates any in-flight job claims under the old
        epoch — late results from the previous incarnation are rejected.
        """
        async with self._lock:
            now_mono = clock.monotonic()
            now_utc = clock.utcnow()
            existing = self.registry.find_by_name(name)
            if existing is not None:
                # Capture the outgoing epoch's summary before resetting
                # counters. Reason is inferred from prior status: an agent
                # that was OFFLINE was reaped by the watchdog (or killed);
                # an agent that was IDLE/BUSY came back via manual restart.
                reason = (
                    "post_offline" if existing.status == AgentStatus.OFFLINE
                    else "manual_reregister"
                )
                existing.epoch_history.append(
                    EpochSummary(
                        epoch=existing.epoch,
                        started_at=existing.epoch_started_at or existing.registered_at,
                        ended_at=now_utc,
                        reason_ended=reason,
                        jobs_claimed=existing.jobs_claimed,
                        jobs_completed=existing.jobs_completed,
                        jobs_failed=existing.jobs_failed,
                    )
                )
                existing.capabilities = list(capabilities)
                existing.epoch += 1
                existing.status = AgentStatus.IDLE
                existing.last_heartbeat_mono = now_mono
                existing.last_heartbeat_at = now_utc
                existing.epoch_started_at = now_utc
                existing.jobs_claimed = 0
                existing.jobs_completed = 0
                existing.jobs_failed = 0
                # Clear any stale current_job pointer; the watchdog already
                # requeued it when this agent went offline.
                existing.current_job_id = None
                self.registry.upsert(existing)
                log.info("agent re-registered name=%s epoch=%d", name, existing.epoch)
                return existing

            agent = Agent(
                name=name,
                capabilities=list(capabilities),
                status=AgentStatus.IDLE,
                epoch=1,
                last_heartbeat_mono=now_mono,
                last_heartbeat_at=now_utc,
                registered_at=now_utc,
                epoch_started_at=now_utc,
            )
            self.registry.upsert(agent)
            log.info("agent registered id=%s name=%s caps=%s", agent.id, name, capabilities)
            return agent

    async def heartbeat(self, agent_id: UUID, epoch: int) -> None:
        """Refresh an agent's last_heartbeat timestamp.

        Raises StaleEpochError if the caller's epoch is older than the agent's
        current epoch (the agent should re-register).
        Raises UnknownAgentError if the id is not in the registry.
        """
        async with self._lock:
            agent = self.registry.get(agent_id)
            if agent is None:
                raise UnknownAgentError(str(agent_id))
            if epoch != agent.epoch:
                raise StaleEpochError(
                    f"agent={agent_id} sent epoch={epoch}, expected {agent.epoch}"
                )
            agent.last_heartbeat_mono = clock.monotonic()
            agent.last_heartbeat_at = clock.utcnow()
            # Heartbeats are how an agent un-marks itself OFFLINE between
            # the watchdog's tick and a re-register. We do not flip status
            # here; re-register is the only path back to IDLE.

    # ----- Job submission -----

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
        branch: str | None = None,
        commit: str | None = None,
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
                branch=branch,
                commit=commit,
                created_at=now,
                history=[JobEvent(at=now, kind="submitted", detail=f"product={product}")],
            )
            self.store.add(job)
            log.info(
                "job submitted id=%s product=%s duration=%s submitter=%s branch=%s commit=%s",
                job.id, product, duration_seconds, submitter, branch, commit,
            )
            return job

    # ----- Atomic claim -----

    async def claim_next_job(self, agent_id: UUID) -> JobAssignment | None:
        """Find a queued job compatible with this agent's capabilities and assign it.

        The match-and-assign is one critical section. Two simultaneous calls
        cannot both claim the same job.

        Raises UnknownAgentError if the agent id is missing, AgentNotIdleError
        if the agent is BUSY or OFFLINE.
        """
        async with self._lock:
            agent = self.registry.get(agent_id)
            if agent is None:
                raise UnknownAgentError(str(agent_id))
            if agent.status != AgentStatus.IDLE:
                raise AgentNotIdleError(f"agent={agent_id} is {agent.status.value}")

            job = self.store.find_queued_for_capabilities(agent.capabilities)
            if job is None:
                return None

            now_utc = clock.utcnow()
            job.status = JobStatus.RUNNING
            job.assigned_agent_id = agent.id
            job.assigned_agent_epoch = agent.epoch
            job.attempt_count += 1
            job.started_at = now_utc
            job.history.append(
                JobEvent(
                    at=now_utc,
                    kind="claimed",
                    agent_id=agent.id,
                    agent_name=agent.name,
                    detail=f"attempt={job.attempt_count}",
                )
            )
            self.store.update(job)   # persist RUNNING transition
            agent.status = AgentStatus.BUSY
            agent.current_job_id = job.id
            agent.jobs_claimed += 1
            log.info(
                "job claimed job=%s by agent=%s (epoch=%d) attempt=%d",
                job.id,
                agent.id,
                agent.epoch,
                job.attempt_count,
            )
            return JobAssignment(
                job_id=job.id,
                product=job.product,
                duration_seconds=job.duration_seconds,
                expected_exit_code=job.expected_exit_code,
                crash_at_pct=job.crash_at_pct,
                slow_multiplier=job.slow_multiplier,
                epoch=agent.epoch,
            )

    # ----- Result reporting -----

    async def report_result(
        self,
        *,
        agent_id: UUID,
        job_id: UUID,
        epoch: int,
        exit_code: int,
        duration_actual: float,
        error_message: str | None = None,
    ) -> None:
        """Record a job's outcome from the agent that ran it.

        Rejects (raises) if:
        * agent or job is unknown
        * caller's epoch is stale (agent has been re-incarnated)
        * job is no longer assigned to this (agent, epoch) pair
        """
        async with self._lock:
            agent = self.registry.get(agent_id)
            if agent is None:
                raise UnknownAgentError(str(agent_id))
            job = self.store.get(job_id)
            if job is None:
                raise UnknownJobError(str(job_id))

            if epoch != agent.epoch:
                self._record_stale_result(job, agent_id=agent_id, epoch=epoch, reason="stale_epoch")
                raise StaleEpochError(
                    f"agent={agent_id} sent epoch={epoch}, expected {agent.epoch}"
                )
            if (
                job.assigned_agent_id != agent.id
                or job.assigned_agent_epoch != epoch
                or job.status != JobStatus.RUNNING
            ):
                self._record_stale_result(
                    job, agent_id=agent_id, epoch=epoch, reason="not_assigned"
                )
                raise JobNotAssignedToAgentError(
                    f"job={job_id} not assigned to agent={agent_id}@epoch={epoch}"
                )

            now = clock.utcnow()
            if exit_code == job.expected_exit_code:
                job.status = JobStatus.COMPLETED
                job.completed_at = now
                job.history.append(
                    JobEvent(
                        at=now,
                        kind="completed",
                        agent_id=agent.id,
                        agent_name=agent.name,
                        detail=f"exit={exit_code} duration={duration_actual:.1f}s",
                    )
                )
                agent.jobs_completed += 1
                self._completion_times.append(clock.monotonic())
                log.info("job completed job=%s by agent=%s", job.id, agent.id)
            else:
                # Job failed at the agent; either retry or give up.
                detail = f"exit={exit_code} attempt={job.attempt_count}/{job.max_attempts}" + (
                    f" err={error_message}" if error_message else ""
                )
                if job.attempt_count >= job.max_attempts:
                    job.status = JobStatus.FAILED
                    job.completed_at = now
                    job.history.append(
                        JobEvent(
                            at=now,
                            kind="failed",
                            agent_id=agent.id,
                            agent_name=agent.name,
                            detail=detail,
                        )
                    )
                    agent.jobs_failed += 1
                    self._completion_times.append(clock.monotonic())
                    log.warning("job failed job=%s exhausted retries", job.id)
                else:
                    job.status = JobStatus.QUEUED
                    job.assigned_agent_id = None
                    job.assigned_agent_epoch = None
                    job.started_at = None
                    job.history.append(
                        JobEvent(
                            at=now,
                            kind="reassigned",
                            agent_id=agent.id,
                            agent_name=agent.name,
                            detail=detail,
                        )
                    )
                    log.info("job re-queued job=%s after agent failure", job.id)

            self.store.update(job)   # persist the outcome

            # Agent is freed regardless of outcome (unless it's still tracking
            # a different job_id, which would indicate state drift).
            if agent.current_job_id == job.id:
                agent.status = AgentStatus.IDLE
                agent.current_job_id = None

    def _record_stale_result(self, job: Job, *, agent_id: UUID, epoch: int, reason: str) -> None:
        job.history.append(
            JobEvent(
                at=clock.utcnow(),
                kind="stale_result_rejected",
                agent_id=agent_id,
                detail=f"epoch={epoch} reason={reason}",
            )
        )
        self.store.update(job)
        log.warning(
            "stale result rejected job=%s agent=%s epoch=%d reason=%s",
            job.id,
            agent_id,
            epoch,
            reason,
        )

    # ----- Watchdog operations -----

    async def reap_stale_agents(self) -> list[UUID]:
        """Mark agents OFFLINE if they have not heartbeated within timeout, and re-queue their jobs.

        Also detects per-job overruns: a job that has been RUNNING longer
        than ``duration_seconds * max_overrun_factor`` is forcibly re-queued
        even if its agent is still heartbeating. This catches stuck-but-alive
        agents that AI-generated heartbeat code might miss.

        Returns the list of agent ids that were marked offline this tick.
        """
        async with self._lock:
            offline_agent_ids: list[UUID] = []
            now_mono = clock.monotonic()
            now_utc = clock.utcnow()

            for agent in list(self.registry):
                if agent.status == AgentStatus.OFFLINE:
                    continue
                if now_mono - agent.last_heartbeat_mono > self.heartbeat_timeout_s:
                    self._mark_offline_locked(agent, now_utc, reason="heartbeat_timeout")
                    offline_agent_ids.append(agent.id)

            # Per-job overrun: a long-running job whose agent is still
            # heartbeating is also pathological. Force-requeue.
            for job in list(self.store):
                if job.status != JobStatus.RUNNING:
                    continue
                if job.started_at is None:
                    continue
                deadline = job.duration_seconds * self.max_overrun_factor
                elapsed = (now_utc - job.started_at).total_seconds()
                if elapsed > deadline:
                    # Capture owner *before* _requeue_job_locked clears it.
                    owner_id = job.assigned_agent_id
                    self._requeue_job_locked(
                        job,
                        now_utc,
                        kind="overrun",
                        detail=f"elapsed={elapsed:.1f}s deadline={deadline:.1f}s",
                    )
                    if owner_id is not None:
                        owner = self.registry.get(owner_id)
                        if owner is not None and owner.current_job_id == job.id:
                            owner.status = AgentStatus.IDLE
                            owner.current_job_id = None

            return offline_agent_ids

    _RequeueKind = Literal["reassigned", "overrun"]

    def _mark_offline_locked(self, agent: Agent, now: datetime, *, reason: str) -> None:
        """Caller must hold self._lock."""
        agent.status = AgentStatus.OFFLINE
        log.warning("agent marked OFFLINE id=%s name=%s reason=%s", agent.id, agent.name, reason)
        if agent.current_job_id is not None:
            job = self.store.get(agent.current_job_id)
            if job is not None and job.status == JobStatus.RUNNING:
                self._requeue_job_locked(
                    job,
                    now,
                    kind="reassigned",
                    detail=f"agent_offline name={agent.name}",
                    agent_id=agent.id,
                    agent_name=agent.name,
                )
        agent.current_job_id = None

    def _requeue_job_locked(
        self,
        job: Job,
        now: datetime,
        *,
        kind: _RequeueKind,
        detail: str,
        agent_id: UUID | None = None,
        agent_name: str | None = None,
    ) -> None:
        """Caller must hold self._lock. Returns job to the queue or fails it."""
        job.assigned_agent_id = None
        job.assigned_agent_epoch = None
        job.started_at = None
        if job.attempt_count >= job.max_attempts:
            job.status = JobStatus.FAILED
            job.completed_at = now
            job.history.append(
                JobEvent(
                    at=now,
                    kind="failed",
                    agent_id=agent_id,
                    agent_name=agent_name,
                    detail=f"{detail} (max_attempts={job.max_attempts})",
                )
            )
            self._completion_times.append(clock.monotonic())
            if agent_id is not None:
                owner = self.registry.get(agent_id)
                if owner is not None:
                    owner.jobs_failed += 1
            log.warning("job failed job=%s after %s", job.id, detail)
        else:
            job.status = JobStatus.QUEUED
            job.history.append(
                JobEvent(
                    at=now,
                    kind=kind,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    detail=detail,
                )
            )
            log.info("job re-queued job=%s reason=%s", job.id, detail)
        self.store.update(job)   # persist requeue/fail transition

    # ----- Demo-only operations -----

    async def force_kill_agent(self, agent_id: UUID) -> None:
        """Simulate immediate disconnect for the dashboard's "Kill" button.

        Marks the agent OFFLINE, re-queues its current job, and bumps its
        epoch so any in-flight result is rejected.
        """
        async with self._lock:
            agent = self.registry.get(agent_id)
            if agent is None:
                raise UnknownAgentError(str(agent_id))
            now_utc = clock.utcnow()
            self._mark_offline_locked(agent, now_utc, reason="killed_by_operator")
            agent.epoch += 1

    # ----- Snapshots for the API -----

    def _throughput_series(
        self, now_mono: float, *, buckets: int = THROUGHPUT_BUCKETS
    ) -> list[int]:
        """Return jobs-per-minute counts for the last ``buckets`` minutes.

        Index 0 is the oldest bucket; index ``buckets - 1`` is the current
        (in-progress) minute. Caller must hold ``self._lock``.
        """
        series = [0] * buckets
        for t in self._completion_times:
            age_min = int((now_mono - t) // 60)
            if 0 <= age_min < buckets:
                series[buckets - 1 - age_min] += 1
        return series

    async def snapshot_fleet(self, *, max_recent_events: int = 30) -> FleetStatusResponse:
        async with self._lock:
            agents = self.registry.all()
            jobs = self.store.all()
            queue = [j for j in jobs if j.status == JobStatus.QUEUED]
            running = [j for j in jobs if j.status == JobStatus.RUNNING]
            terminal = [
                j for j in jobs
                if j.status in (JobStatus.COMPLETED, JobStatus.FAILED)
            ]
            terminal.sort(
                key=lambda j: j.completed_at or j.created_at, reverse=True,
            )
            recent_completed = terminal[:20]

            recent: list[dict[str, object]] = []
            for j in jobs:
                for ev in j.history:
                    recent.append(
                        {
                            "at": ev.at.isoformat(),
                            "kind": ev.kind,
                            "job_id": str(j.id),
                            "product": j.product,
                            "submitter": j.submitter,
                            "agent_id": str(ev.agent_id) if ev.agent_id else None,
                            "agent_name": ev.agent_name,
                            "detail": ev.detail,
                        }
                    )
            recent.sort(key=lambda e: str(e["at"]), reverse=True)
            recent = recent[:max_recent_events]

            stats = FleetStats(
                total_agents=len(agents),
                idle=sum(1 for a in agents if a.status == AgentStatus.IDLE),
                busy=sum(1 for a in agents if a.status == AgentStatus.BUSY),
                offline=sum(1 for a in agents if a.status == AgentStatus.OFFLINE),
                queue_depth=len(queue),
                jobs_running=len(running),
                jobs_completed=sum(1 for j in jobs if j.status == JobStatus.COMPLETED),
                jobs_failed=sum(1 for j in jobs if j.status == JobStatus.FAILED),
                throughput_per_min=self._throughput_series(clock.monotonic()),
            )
            return FleetStatusResponse(
                agents=agents,
                queue=queue,
                running_jobs=running,
                recent_completed=recent_completed,
                recent_events=recent,
                stats=stats,
            )
