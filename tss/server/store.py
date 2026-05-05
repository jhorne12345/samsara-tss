"""Job store — pure data, no locking.

Jobs are kept in insertion order so the queue is naturally FIFO. The store
exposes only the queries the Dispatcher needs; complex reporting is built
on top of ``all()`` rather than baked in here.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Protocol
from uuid import UUID

from tss.common.models import Job, JobStatus


class JobStore(Protocol):
    def add(self, job: Job) -> None: ...
    def get(self, job_id: UUID) -> Job | None: ...
    def all(self) -> list[Job]: ...
    def by_status(self, status: JobStatus) -> list[Job]: ...
    def find_queued_for_capabilities(self, capabilities: Iterable[str]) -> Job | None: ...
    def __iter__(self) -> Iterator[Job]: ...
    def __len__(self) -> int: ...


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[UUID, Job] = {}

    def add(self, job: Job) -> None:
        self._jobs[job.id] = job

    def get(self, job_id: UUID) -> Job | None:
        return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        return list(self._jobs.values())

    def by_status(self, status: JobStatus) -> list[Job]:
        return [j for j in self._jobs.values() if j.status == status]

    def find_queued_for_capabilities(self, capabilities: Iterable[str]) -> Job | None:
        caps = set(capabilities)
        for job in self._jobs.values():
            if job.status == JobStatus.QUEUED and job.product in caps:
                return job
        return None

    def __iter__(self) -> Iterator[Job]:
        return iter(self._jobs.values())

    def __len__(self) -> int:
        return len(self._jobs)
