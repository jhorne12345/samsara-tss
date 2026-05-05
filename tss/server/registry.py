"""Agent registry — pure data, no locking.

The registry stores Agent records keyed by id. All concurrent access is
serialized by the Dispatcher's lock; this module is intentionally not
thread-safe on its own. Keeping the lock in one place (Dispatcher) avoids
the per-resource-lock anti-pattern flagged in CLAUDE.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol
from uuid import UUID

from tss.common.models import Agent


class AgentRegistry(Protocol):
    """Storage seam for swapping in-memory for a real database later."""

    def upsert(self, agent: Agent) -> None: ...
    def get(self, agent_id: UUID) -> Agent | None: ...
    def remove(self, agent_id: UUID) -> Agent | None: ...
    def all(self) -> list[Agent]: ...
    def find_by_name(self, name: str) -> Agent | None: ...
    def __iter__(self) -> Iterator[Agent]: ...
    def __len__(self) -> int: ...


class InMemoryAgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[UUID, Agent] = {}

    def upsert(self, agent: Agent) -> None:
        self._agents[agent.id] = agent

    def get(self, agent_id: UUID) -> Agent | None:
        return self._agents.get(agent_id)

    def remove(self, agent_id: UUID) -> Agent | None:
        return self._agents.pop(agent_id, None)

    def all(self) -> list[Agent]:
        return list(self._agents.values())

    def find_by_name(self, name: str) -> Agent | None:
        for agent in self._agents.values():
            if agent.name == name:
                return agent
        return None

    def __iter__(self) -> Iterator[Agent]:
        return iter(self._agents.values())

    def __len__(self) -> int:
        return len(self._agents)
