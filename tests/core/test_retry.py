from __future__ import annotations

import pytest

from tdpy.core.errors import BadRequest, FloodWait, InternalError, NotFound, Unauthorized
from tdpy.core.retry import RetryPolicy

DETERMINISTIC = RetryPolicy(jitter=0.0)


def test_flood_wait_is_honoured_exactly() -> None:
    # TDLib's number is a floor. Shortening it escalates the limit.
    assert DETERMINISTIC.delay_for(FloodWait(429, "x", retry_after=7.0), 1) == 7.0


def test_flood_wait_longer_than_the_ceiling_is_not_retried() -> None:
    policy = RetryPolicy(max_flood_wait=60.0, jitter=0.0)

    assert policy.delay_for(FloodWait(429, "x", retry_after=3600.0), 1) is None


def test_server_errors_back_off_exponentially() -> None:
    policy = RetryPolicy(max_attempts=5, base_backoff=0.5, jitter=0.0)
    delays = [policy.delay_for(InternalError(500, "x"), n) for n in (1, 2, 3, 4)]

    assert delays == [0.5, 1.0, 2.0, 4.0]


def test_backoff_stops_growing_at_the_ceiling() -> None:
    policy = RetryPolicy(max_attempts=99, base_backoff=1.0, max_backoff=8.0, jitter=0.0)

    assert policy.delay_for(InternalError(500, "x"), 10) == 8.0


def test_server_errors_can_be_opted_out_of() -> None:
    policy = RetryPolicy(retry_server_errors=False, jitter=0.0)

    assert policy.delay_for(InternalError(500, "x"), 1) is None


@pytest.mark.parametrize(
    "error", [BadRequest(400, "x"), NotFound(404, "x"), Unauthorized(401, "x")]
)
def test_client_errors_are_never_retried(error: Exception) -> None:
    # Retrying a rejected request just sends the same bad request again.
    assert DETERMINISTIC.delay_for(error, 1) is None  # type: ignore[arg-type]


def test_attempts_are_capped() -> None:
    policy = RetryPolicy(max_attempts=3, jitter=0.0)
    error = FloodWait(429, "x", retry_after=1.0)

    assert policy.delay_for(error, 2) == 1.0
    assert policy.delay_for(error, 3) is None


def test_jitter_only_ever_lengthens_the_wait() -> None:
    policy = RetryPolicy(max_attempts=99, jitter=0.5)
    error = FloodWait(429, "x", retry_after=10.0)
    samples = [policy.delay_for(error, 1) for _ in range(50)]

    assert all(sample is not None and 10.0 <= sample <= 15.0 for sample in samples)
    assert len(set(samples)) > 1, "jitter should actually vary"


def test_policies_are_immutable() -> None:
    with pytest.raises(AttributeError):
        DETERMINISTIC.max_attempts = 99  # type: ignore[misc]
