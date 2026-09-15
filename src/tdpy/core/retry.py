"""Opt-in retry policy.

Deliberately not on by default. Most TDLib methods are not idempotent --
retrying ``sendMessage`` after an ambiguous failure can post the message twice
-- so the caller decides which calls are safe to repeat.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from tdpy.core.errors import FloodWait, InternalError, TDLibError

__all__ = ["RetryPolicy"]


@dataclass(frozen=True)
class RetryPolicy:
    """Decides whether and how long to wait before trying again.

    Args:
        max_attempts: Total attempts including the first.
        max_flood_wait: Give up on a :class:`~tdpy.core.errors.FloodWait`
            asking for longer than this. Sleeping for an hour inside a request
            is almost never what the caller wanted.
        base_backoff: First delay for server errors; doubles each attempt.
        max_backoff: Ceiling for the doubling.
        jitter: Fraction of the delay added at random, to stop many clients
            retrying in lockstep. Set to 0 for deterministic tests.
        retry_server_errors: Whether 5xx responses are retried at all.
    """

    max_attempts: int = 3
    max_flood_wait: float = 60.0
    base_backoff: float = 0.5
    max_backoff: float = 30.0
    jitter: float = 0.1
    retry_server_errors: bool = True

    def delay_for(self, error: TDLibError, attempt: int) -> float | None:
        """Seconds to sleep before attempt ``attempt + 1``, or ``None`` to stop.

        Args:
            error: The failure just seen.
            attempt: How many attempts have been made, starting at 1.
        """
        if attempt >= self.max_attempts:
            return None

        if isinstance(error, FloodWait):
            if error.retry_after > self.max_flood_wait:
                return None
            # TDLib's number is a floor, not an estimate; never shorten it.
            return self._jittered(error.retry_after)

        if isinstance(error, InternalError) and self.retry_server_errors:
            delay = min(self.base_backoff * 2 ** (attempt - 1), self.max_backoff)
            return self._jittered(delay)

        return None

    def _jittered(self, delay: float) -> float:
        if self.jitter <= 0:
            return delay
        return delay + random.uniform(0.0, self.jitter * delay)
