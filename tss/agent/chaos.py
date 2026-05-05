"""Chaos profiles for the mock agent.

Each profile is a probability distribution over the four failure modes called
out in the assessment plan:

* ``silent_death``: agent stops sending heartbeats entirely.
* ``partition``: agent skips heartbeats for a stretch (network partition).
* ``job_crash``: agent reports a job as failed at a random progress point.
* ``slow_exec``: agent overruns the declared duration.

Profiles are drawn from at agent startup; the actual rolls happen inside the
agent's per-tick / per-job logic.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

ChaosIntensity = Literal["stable", "flaky", "crashy", "doomed", "mixed"]


@dataclass
class ChaosProfile:
    silent_death_prob_per_minute: float = 0.0
    partition_prob_per_heartbeat: float = 0.0
    partition_duration_s_range: tuple[float, float] = (5.0, 15.0)
    job_crash_prob: float = 0.0
    job_crash_at_pct_range: tuple[float, float] = (0.1, 0.9)
    slow_exec_prob: float = 0.0
    slow_multiplier_range: tuple[float, float] = (2.0, 5.0)

    def roll_silent_death(self, dt_seconds: float, rng: random.Random) -> bool:
        """Return True if the agent should silently die in this tick window."""
        if self.silent_death_prob_per_minute <= 0:
            return False
        per_second = self.silent_death_prob_per_minute / 60.0
        prob = min(1.0, per_second * dt_seconds)
        return rng.random() < prob

    def roll_partition(self, rng: random.Random) -> float | None:
        """Return partition duration in seconds, or None to not partition."""
        if self.partition_prob_per_heartbeat <= 0:
            return None
        if rng.random() >= self.partition_prob_per_heartbeat:
            return None
        lo, hi = self.partition_duration_s_range
        return rng.uniform(lo, hi)

    def roll_job_crash(self, rng: random.Random) -> float | None:
        """Return crash_at_pct, or None to run the job to completion."""
        if self.job_crash_prob <= 0:
            return None
        if rng.random() >= self.job_crash_prob:
            return None
        lo, hi = self.job_crash_at_pct_range
        return rng.uniform(lo, hi)

    def roll_slow_exec(self, rng: random.Random) -> float:
        """Return a multiplier for declared duration (1.0 = on time)."""
        if self.slow_exec_prob <= 0:
            return 1.0
        if rng.random() >= self.slow_exec_prob:
            return 1.0
        lo, hi = self.slow_multiplier_range
        return rng.uniform(lo, hi)


PROFILES: dict[str, ChaosProfile] = {
    "stable": ChaosProfile(),
    "flaky": ChaosProfile(
        partition_prob_per_heartbeat=0.05,
        slow_exec_prob=0.2,
    ),
    "crashy": ChaosProfile(
        job_crash_prob=0.3,
        slow_exec_prob=0.1,
    ),
    "doomed": ChaosProfile(
        silent_death_prob_per_minute=0.5,
        job_crash_prob=0.2,
    ),
}


def profile_for_intensity(intensity: ChaosIntensity, rng: random.Random) -> ChaosProfile:
    """Return a profile by name. ``mixed`` randomly chooses one of the named profiles."""
    if intensity == "mixed":
        choice = rng.choice(list(PROFILES.keys()))
        return PROFILES[choice]
    return PROFILES[intensity]
