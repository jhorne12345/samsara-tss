"""Job store protocol.

Jobs are kept in submission order so the queue is naturally FIFO. The store
exposes only the queries the Dispatcher needs; complex reporting is built on
top of ``all()`` rather than baked in here.

Implementation:
- ``SQLiteJobStore`` in ``tss.server.sqlite_store`` — canonical, used in
  production and tests (``:memory:`` mode for tests).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Protocol
from uuid import UUID

from tss.common.models import Job, JobStatus


class JobStore(Protocol):
    def add(self, job: Job) -> None: ...
    def update(self, job: Job) -> None: ...
    def get(self, job_id: UUID) -> Job | None: ...
    def all(self) -> list[Job]: ...
    def by_status(self, status: JobStatus) -> list[Job]: ...
    def find_queued_for_capabilities(self, capabilities: Iterable[str]) -> Job | None: ...
    def __iter__(self) -> Iterator[Job]: ...
    def __len__(self) -> int: ...
