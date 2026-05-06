"""Pydantic models that flow over the wire and within the dispatcher.

The data model is the contract between the dispatcher, agents, CLI, and
dashboard. Status enums are string-valued so the dashboard can render them
directly without a translation layer.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class AgentStatus(StrEnum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


JobEventKind = Literal[
    "submitted",
    "claimed",
    "completed",
    "failed",
    "reassigned",
    "overrun",
    "stale_result_rejected",
]


class JobEvent(BaseModel):
    """An auditable transition in a job's lifecycle.

    Appended in-place; the dashboard's "Recent events" panel is built from
    these. Events are intentionally cheap to add — they are the visibility
    surface for resiliency behavior.
    """

    model_config = ConfigDict(frozen=True)

    at: datetime
    kind: JobEventKind
    agent_id: UUID | None = None
    agent_name: str | None = None
    detail: str | None = None


class Agent(BaseModel):
    """Server-side representation of a registered testbed."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    capabilities: list[str]
    status: AgentStatus = AgentStatus.IDLE
    epoch: int = 1
    last_heartbeat_mono: float
    """Monotonic seconds at last heartbeat. Used for timeout math."""
    last_heartbeat_at: datetime
    """Wall-clock time of last heartbeat. For humans."""
    current_job_id: UUID | None = None
    registered_at: datetime


class Job(BaseModel):
    """A test job that needs an agent with the right capability."""

    id: UUID = Field(default_factory=uuid4)
    product: str
    duration_seconds: float = Field(gt=0)
    expected_exit_code: int = 0
    crash_at_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    """For demos: agent should crash partway through this fraction of duration."""
    slow_multiplier: float = Field(default=1.0, ge=0.1)
    """For demos: agent should take this many times the declared duration."""
    submitter: str
    """Who submitted this job. Honor system; populated by CLI ($USER) or web ui (localStorage)."""

    status: JobStatus = JobStatus.QUEUED
    assigned_agent_id: UUID | None = None
    assigned_agent_epoch: int | None = None
    """Recorded at claim time. Used to reject stale results from a previous incarnation."""

    attempt_count: int = 0
    max_attempts: int = 3

    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    history: list[JobEvent] = Field(default_factory=list)


# ----- API request / response payloads -----


class RegisterRequest(BaseModel):
    name: str
    capabilities: list[str] = Field(min_length=1)


class RegisterResponse(BaseModel):
    agent_id: UUID
    epoch: int
    heartbeat_interval_s: float
    poll_interval_s: float


class HeartbeatRequest(BaseModel):
    epoch: int


class JobAssignment(BaseModel):
    """Trimmed Job payload sent to an agent when it claims a job."""

    job_id: UUID
    product: str
    duration_seconds: float
    expected_exit_code: int
    crash_at_pct: float | None
    slow_multiplier: float
    epoch: int
    """The agent's epoch at the time of claim. Agent echoes this on result."""


class JobResultRequest(BaseModel):
    epoch: int
    exit_code: int
    duration_actual: float
    error_message: str | None = None


class JobSubmitRequest(BaseModel):
    product: str
    duration_seconds: float = Field(gt=0)
    expected_exit_code: int = 0
    crash_at_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    slow_multiplier: float = Field(default=1.0, ge=0.1)
    max_attempts: int = Field(default=3, ge=1, le=10)
    submitter: str = "unknown"
    """Free-form identifier. Set by CLI (defaults to $USER) or web (localStorage)."""


class JobSubmitResponse(BaseModel):
    job_id: UUID


class FleetStats(BaseModel):
    total_agents: int
    idle: int
    busy: int
    offline: int
    queue_depth: int
    jobs_running: int
    jobs_completed: int
    jobs_failed: int


class FleetStatusResponse(BaseModel):
    agents: list[Agent]
    queue: list[Job]
    running_jobs: list[Job]
    recent_completed: list[Job] = Field(default_factory=list)
    """Most recently terminal jobs (completed or failed), newest first."""
    recent_events: list[dict[str, object]]
    """Recent events flattened across all jobs, newest first."""
    stats: FleetStats


class AgentHistoryEvent(BaseModel):
    """A single job-level event filtered to one agent for the history view."""

    at: datetime
    kind: JobEventKind
    job_id: UUID
    product: str
    detail: str | None = None


class AgentHistoryResponse(BaseModel):
    agent: Agent
    events: list[AgentHistoryEvent]
    """Events touching this agent, newest first."""
