from __future__ import annotations

import pytest

from tdpy.core.errors import (
    BadRequest,
    FloodWait,
    Forbidden,
    InternalError,
    NotFound,
    TDLibError,
    Unauthorized,
    error_from_payload,
)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (400, BadRequest),
        (401, Unauthorized),
        (403, Forbidden),
        (404, NotFound),
        (406, BadRequest),
        (500, InternalError),
        (503, InternalError),
        (0, TDLibError),
        (42, TDLibError),
    ],
)
def test_code_selects_the_exception_class(code: int, expected: type[TDLibError]) -> None:
    assert type(error_from_payload({"code": code, "message": "x"})) is expected


@pytest.mark.parametrize(
    ("message", "seconds"),
    [
        ("FLOOD_WAIT_30", 30.0),
        ("Too Many Requests: retry after 17", 17.0),
        ("retry after 5", 5.0),
        ("RETRY AFTER 9", 9.0),
    ],
)
def test_rate_limits_expose_the_delay_as_a_number(message: str, seconds: float) -> None:
    error = error_from_payload({"code": 429, "message": message})

    assert isinstance(error, FloodWait)
    assert error.retry_after == seconds


def test_flood_wait_is_detected_from_the_message_alone() -> None:
    # TDLib does not always use 420/429 for MTProto flood waits.
    error = error_from_payload({"code": 400, "message": "FLOOD_WAIT_12"})

    assert isinstance(error, FloodWait)
    assert error.retry_after == 12.0


def test_unparsable_rate_limit_still_classifies_as_flood_wait() -> None:
    error = error_from_payload({"code": 429, "message": "Too Many Requests"})

    assert isinstance(error, FloodWait)
    assert error.retry_after == 0.0, "0 lets the caller back off rather than guess"


def test_error_carries_the_request_that_caused_it() -> None:
    error = error_from_payload({"code": 400, "message": "CHAT_NOT_FOUND"}, {"@type": "getChat"})

    assert error.request == {"@type": "getChat"}
    assert "getChat" in str(error)
    assert "CHAT_NOT_FOUND" in str(error)


def test_missing_fields_do_not_crash_the_factory() -> None:
    error = error_from_payload({})

    assert error.code == 0
    assert error.message == ""


def test_flood_wait_message_states_the_delay() -> None:
    assert "retry after 30s" in str(FloodWait(429, "FLOOD_WAIT_30", retry_after=30.0))


def test_every_tdlib_error_is_catchable_as_the_base() -> None:
    from tdpy.exceptions import TdpyError

    with pytest.raises(TdpyError):
        raise error_from_payload({"code": 400, "message": "x"})
