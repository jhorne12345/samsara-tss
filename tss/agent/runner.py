"""Mock test agent (testbed simulator).

Runs as a long-lived async process that:

1. Registers with the dispatcher and remembers ``(agent_id, epoch)``.
2. Sends heartbeats every ``heartbeat_interval_s``.
3. Polls ``/jobs/next`` every ``poll_interval_s`` when idle.
4. When given a job, "runs" it by sleeping (with optional crash / slow chaos).
5. Reports the result back; on 410 Gone, re-registers and resumes.

The chaos profile drives random failure injection. With the default ``stable``
profile, agents behave normally and complete every job.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx

from tss.agent.chaos import ChaosProfile

log = logging.getLogger(__name__)


@dataclass
class AgentRunner:
    name: str
    capabilities: list[str]
    dispatcher_url: str = "http://127.0.0.1:8080"
    chaos: ChaosProfile = field(default_factory=ChaosProfile)
    rng: random.Random = field(default_factory=random.Random)

    # Populated after register()
    agent_id: UUID | None = None
    epoch: int = 0
    heartbeat_interval_s: float = 2.0
    poll_interval_s: float = 1.0

    # Internal
    _client: httpx.AsyncClient | None = None
    _stop_event: asyncio.Event | None = None
    _silent: bool = False
    """When True, the agent stops sending heartbeats and stops polling. Used
    to simulate a hard crash without exiting the process (so the chaos run
    can keep going)."""
    _partition_until_mono: float = 0.0
    """If the monotonic clock is below this value, the agent skips heartbeats
    and polls (simulating a network partition)."""

    async def __aenter__(self) -> AgentRunner:
        self._client = httpx.AsyncClient(base_url=self.dispatcher_url, timeout=5.0)
        self._stop_event = asyncio.Event()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._client = None

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    async def register(self) -> None:
        assert self._client is not None
        r = await self._client.post(
            "/api/agents/register",
            json={"name": self.name, "capabilities": self.capabilities},
        )
        r.raise_for_status()
        body = r.json()
        self.agent_id = UUID(body["agent_id"])
        self.epoch = body["epoch"]
        self.heartbeat_interval_s = body["heartbeat_interval_s"]
        self.poll_interval_s = body["poll_interval_s"]
        log.info("agent registered name=%s id=%s epoch=%d", self.name, self.agent_id, self.epoch)

    async def run(self) -> None:
        """Main loop. Runs until stop() is called or an unrecoverable error."""
        if self._client is None or self._stop_event is None:
            raise RuntimeError("AgentRunner must be used as async context manager")
        if self.agent_id is None:
            await self.register()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._heartbeat_loop(), name=f"hb-{self.name}")
            tg.create_task(self._work_loop(), name=f"work-{self.name}")
            tg.create_task(self._wait_for_stop(), name=f"stop-{self.name}")

    async def _wait_for_stop(self) -> None:
        assert self._stop_event is not None
        await self._stop_event.wait()
        # Force the other tasks to wake up by raising a marker exception in the group.
        raise asyncio.CancelledError()

    async def _heartbeat_loop(self) -> None:
        assert self._client is not None and self._stop_event is not None
        last_tick = asyncio.get_event_loop().time()
        while not self._stop_event.is_set():
            now = asyncio.get_event_loop().time()
            dt = now - last_tick
            last_tick = now

            # Roll chaos: silent death is permanent in this run.
            if not self._silent and self.chaos.roll_silent_death(dt, self.rng):
                log.warning("agent %s silently dying (chaos)", self.name)
                self._silent = True

            partition = self.chaos.roll_partition(self.rng)
            if partition is not None:
                self._partition_until_mono = max(
                    self._partition_until_mono,
                    asyncio.get_event_loop().time() + partition,
                )
                log.info("agent %s partitioning for %.1fs", self.name, partition)

            if not self._silent and now >= self._partition_until_mono:
                try:
                    await self._send_heartbeat()
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 410:
                        log.info("agent %s got 410 on heartbeat, re-registering", self.name)
                        await self.register()
                except httpx.RequestError as e:
                    log.warning("agent %s heartbeat network error: %s", self.name, e)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.heartbeat_interval_s)
            except TimeoutError:
                continue
            else:
                break

    async def _send_heartbeat(self) -> None:
        assert self._client is not None and self.agent_id is not None
        r = await self._client.post(
            f"/api/agents/{self.agent_id}/heartbeat",
            json={"epoch": self.epoch},
        )
        r.raise_for_status()

    async def _work_loop(self) -> None:
        assert self._client is not None and self._stop_event is not None
        while not self._stop_event.is_set():
            if self._silent or asyncio.get_event_loop().time() < self._partition_until_mono:
                # Pretend to be down — neither heartbeat nor poll.
                await asyncio.sleep(self.poll_interval_s)
                continue
            assignment = await self._poll_for_job()
            if assignment is None:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_s)
                except TimeoutError:
                    continue
                else:
                    break
            await self._run_job(assignment)

    async def _poll_for_job(self) -> dict[str, Any] | None:
        assert self._client is not None and self.agent_id is not None
        try:
            r = await self._client.get(f"/api/agents/{self.agent_id}/jobs/next")
        except httpx.RequestError as e:
            log.warning("agent %s poll network error: %s", self.name, e)
            return None
        if r.status_code == 204:
            return None
        if r.status_code == 410:
            log.info("agent %s got 410 on poll, re-registering", self.name)
            await self.register()
            return None
        if r.status_code == 409:
            # Agent thinks it's busy from the dispatcher's POV; happens after a
            # missed result post. Wait a tick and retry.
            return None
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def _run_job(self, assignment: dict[str, Any]) -> None:
        """Simulate running a test job. Sleep for the duration, then post a result.

        Chaos hooks: chaos.roll_slow_exec multiplies duration; chaos.roll_job_crash
        causes a non-zero exit at a fraction through. The job's own
        ``crash_at_pct`` and ``slow_multiplier`` (from a chaos-crafted submission)
        also apply, layered on top.
        """
        assert self._client is not None and self.agent_id is not None
        job_id = assignment["job_id"]
        epoch_at_claim = assignment["epoch"]
        declared = float(assignment["duration_seconds"])
        expected_exit = int(assignment["expected_exit_code"])

        # Layer chaos: per-job overrides from submission, then per-agent profile.
        job_slow = float(assignment.get("slow_multiplier") or 1.0)
        agent_slow = self.chaos.roll_slow_exec(self.rng)
        slow_mul = job_slow * agent_slow

        job_crash_at = assignment.get("crash_at_pct")
        agent_crash_at = self.chaos.roll_job_crash(self.rng)
        crash_at = job_crash_at if job_crash_at is not None else agent_crash_at

        run_duration = declared * slow_mul

        log.info(
            "agent %s running job=%s declared=%.1fs actual=%.1fs crash_at=%s",
            self.name,
            job_id,
            declared,
            run_duration,
            crash_at,
        )

        loop_start = asyncio.get_event_loop().time()
        try:
            if crash_at is not None:
                crash_t = run_duration * float(crash_at)
                await asyncio.sleep(crash_t)
                actual = asyncio.get_event_loop().time() - loop_start
                # Report a non-zero exit
                exit_code = 1 if expected_exit == 0 else 0
                await self._post_result(
                    job_id=UUID(job_id),
                    epoch=epoch_at_claim,
                    exit_code=exit_code,
                    duration_actual=actual,
                    error_message=f"chaos crash at {crash_at:.2f} of duration",
                )
                return

            await asyncio.sleep(run_duration)
            actual = asyncio.get_event_loop().time() - loop_start
            await self._post_result(
                job_id=UUID(job_id),
                epoch=epoch_at_claim,
                exit_code=expected_exit,
                duration_actual=actual,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("agent %s unexpected error during job: %s", self.name, e)

    async def _post_result(
        self,
        *,
        job_id: UUID,
        epoch: int,
        exit_code: int,
        duration_actual: float,
        error_message: str | None = None,
    ) -> None:
        assert self._client is not None and self.agent_id is not None
        try:
            r = await self._client.post(
                f"/api/agents/{self.agent_id}/jobs/{job_id}/result",
                json={
                    "epoch": epoch,
                    "exit_code": exit_code,
                    "duration_actual": duration_actual,
                    "error_message": error_message,
                },
            )
        except httpx.RequestError as e:
            log.warning("agent %s result post network error: %s", self.name, e)
            return
        if r.status_code == 410:
            log.info("agent %s got 410 on result, re-registering", self.name)
            await self.register()
            return
        if r.status_code == 409:
            log.warning(
                "agent %s result rejected (stale): job=%s epoch=%d", self.name, job_id, epoch
            )
            return
        r.raise_for_status()


async def run_one_agent(
    *,
    name: str,
    capabilities: list[str],
    dispatcher_url: str,
    chaos: ChaosProfile | None = None,
    seed: int | None = None,
) -> None:
    """Convenience wrapper: build an AgentRunner and run it until cancelled."""
    rng = random.Random(seed)
    profile = chaos or ChaosProfile()
    async with AgentRunner(
        name=name,
        capabilities=capabilities,
        dispatcher_url=dispatcher_url,
        chaos=profile,
        rng=rng,
    ) as agent:
        try:
            await agent.run()
        except* asyncio.CancelledError:
            pass
