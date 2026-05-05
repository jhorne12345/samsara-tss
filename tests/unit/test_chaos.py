"""Unit tests for chaos profile sampling."""

from __future__ import annotations

import random

from tss.agent.chaos import PROFILES, ChaosProfile, profile_for_intensity


def test_stable_profile_never_rolls():
    rng = random.Random(0)
    p = PROFILES["stable"]
    for _ in range(1000):
        assert not p.roll_silent_death(60.0, rng)
        assert p.roll_partition(rng) is None
        assert p.roll_job_crash(rng) is None
        assert p.roll_slow_exec(rng) == 1.0


def test_silent_death_fires_eventually_for_doomed():
    """A doomed profile (50%/min) should fire within several seconds."""
    rng = random.Random(42)
    p = PROFILES["doomed"]
    fired = False
    for _ in range(100):
        if p.roll_silent_death(2.0, rng):
            fired = True
            break
    assert fired


def test_crashy_profile_returns_crash_pct_in_range():
    rng = random.Random(7)
    p = PROFILES["crashy"]
    crash_pcts = [p.roll_job_crash(rng) for _ in range(1000)]
    fired = [c for c in crash_pcts if c is not None]
    # crashy has 30% crash prob, so we should see roughly 300 crashes out of 1000.
    assert 200 <= len(fired) <= 400, f"unexpected crash rate: {len(fired)}/1000"
    for c in fired:
        lo, hi = p.job_crash_at_pct_range
        assert lo <= c <= hi


def test_slow_exec_multiplier_in_range_or_one():
    rng = random.Random(9)
    p = PROFILES["flaky"]
    multipliers = [p.roll_slow_exec(rng) for _ in range(1000)]
    slowed = [m for m in multipliers if m != 1.0]
    on_time = [m for m in multipliers if m == 1.0]
    # flaky has 0.2 slow_exec_prob.
    assert 100 <= len(slowed) <= 300
    assert len(on_time) >= 700
    for m in slowed:
        lo, hi = p.slow_multiplier_range
        assert lo <= m <= hi


def test_mixed_intensity_returns_a_named_profile():
    rng = random.Random(0)
    seen = set()
    for _ in range(50):
        p = profile_for_intensity("mixed", rng)
        # It must be one of the named profiles
        assert any(p == named for named in PROFILES.values())
        seen.add(id(p))
    # Over 50 draws we should hit at least 2 distinct profiles.
    assert len(seen) >= 2


def test_specific_intensity_returns_that_profile():
    rng = random.Random(0)
    for name in ("stable", "flaky", "crashy", "doomed"):
        p = profile_for_intensity(name, rng)  # type: ignore[arg-type]
        assert p is PROFILES[name]


def test_partition_duration_within_declared_range():
    rng = random.Random(1)
    p = ChaosProfile(
        partition_prob_per_heartbeat=1.0,  # always fires
        partition_duration_s_range=(2.0, 5.0),
    )
    for _ in range(100):
        d = p.roll_partition(rng)
        assert d is not None
        assert 2.0 <= d <= 5.0
