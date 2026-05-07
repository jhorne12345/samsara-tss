"""Typed dispatcher errors that map cleanly to HTTP status codes."""

from __future__ import annotations


class DispatcherError(Exception):
    """Base for all dispatcher-level errors."""


class UnknownAgentError(DispatcherError):
    """Agent id is not in the registry."""


class StaleEpochError(DispatcherError):
    """Caller's epoch does not match the agent's current epoch.

    This happens when an agent that was marked offline tries to use its old
    credentials. The agent must re-register to obtain a new epoch.
    """


class AgentNotIdleError(DispatcherError):
    """Agent attempted to claim a job but is not in IDLE state."""


class UnknownJobError(DispatcherError):
    """Job id does not exist."""


class JobNotAssignedToAgentError(DispatcherError):
    """A different (or no) agent owns the job. Used to reject stale results.

    This is the race-condition case: agent A claimed job J, was marked offline,
    J was reassigned to agent B, and now A's late result is arriving. Reject.
    """


class AgentQuarantinedError(DispatcherError):
    """A demo-killed agent is in its quarantine window and may not re-register.

    Set by ``force_kill_agent`` to keep killed agents off the fleet long enough
    to see their job get reassigned. Mapped to HTTP 423 Locked so the runner
    can distinguish this from a normal failure and back off.
    """
