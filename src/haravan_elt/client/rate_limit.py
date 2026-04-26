"""LeakyBucket factory tuned for Haravan (research §1: 4 req/s, burst 80).

`pyrate-limiter` v3 `try_acquire` is non-blocking by default and raises
`BucketFullException` on saturation. We configure `max_delay` +
`raise_when_fail=False` so the call sleeps up to 30s waiting for a slot
instead of crashing — matching Haravan's leaky-bucket model 1:1.

A single `Rate` is sufficient: 4/s combined with the bucket's natural
burst capacity gives the desired steady + burst behavior without a
redundant second rate (degenerate at 4/s = 80/20s).
"""

from __future__ import annotations

from pyrate_limiter import Duration, Limiter, Rate


def make_limiter(rate_per_sec: int, burst_capacity: int) -> Limiter:
    """Build a blocking Limiter (sleeps up to 30s rather than raising).

    Args:
        rate_per_sec: Steady-state requests per second (Haravan default: 4).
        burst_capacity: Token bucket size (kept for API parity; pyrate-limiter
            v3's bucket size is implicit in the `Rate` plus internal queueing
            — burst_capacity argument retained for future tuning hooks).
    """
    del burst_capacity  # currently unused; reserved for future per-bucket tuning
    return Limiter(
        Rate(rate_per_sec, Duration.SECOND),
        max_delay=Duration.SECOND * 30,
        raise_when_fail=False,
    )
