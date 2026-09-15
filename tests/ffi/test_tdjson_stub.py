"""Exercise the ctypes wrapper's own logic against a stub library.

Everything here would otherwise only be covered by ``test_tdjson.py``, which
skips on machines without a compiled TDLib -- including CI. The two invariants
this file protects (pointer copying and single-threaded receive) are the ones
whose failure mode is memory corruption rather than a test failure.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from typing import Any

import pytest

from tdpy._ffi.tdjson import ConcurrentReceiveError, TDJson, get_tdjson, reset_tdjson


class StubFunction:
    """Stands in for a ctypes ``_FuncPtr``: assignable metadata, callable."""

    def __init__(self, impl: Any) -> None:
        self.impl = impl
        self.restype: Any = None
        self.argtypes: Any = None
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any) -> Any:
        self.calls.append(args)
        return self.impl(*args)


class StubLib:
    def __init__(self) -> None:
        self.next_client_id = 0
        self.received: list[bytes | None] = []
        self.td_create_client_id = StubFunction(self._create_client_id)
        self.td_send = StubFunction(lambda _cid, _req: None)
        self.td_receive = StubFunction(self._receive)
        self.td_execute = StubFunction(lambda _req: b'{"@type":"ok"}')
        self.td_set_log_message_callback = StubFunction(lambda _v, _cb: None)

    def _create_client_id(self) -> int:
        self.next_client_id += 1
        return self.next_client_id

    def _receive(self, _timeout: Any) -> bytes | None:
        return self.received.pop(0) if self.received else None


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> StubLib:
    lib = StubLib()
    monkeypatch.setattr("tdpy._ffi.tdjson.load_tdjson", lambda _p=None: (lib, "stub://tdjson"))
    return lib


@pytest.fixture
def td(stub: StubLib) -> TDJson:
    return TDJson()


def test_records_the_library_spec_that_worked(td: TDJson) -> None:
    assert td.library_path == "stub://tdjson"


def test_binds_the_documented_signatures(td: TDJson, stub: StubLib) -> None:
    assert stub.td_create_client_id.restype is ctypes.c_int
    assert stub.td_send.argtypes == [ctypes.c_int, ctypes.c_char_p]
    # c_char_p is what makes ctypes copy the buffer before TDLib frees it.
    assert stub.td_receive.restype is ctypes.c_char_p
    assert stub.td_receive.argtypes == [ctypes.c_double]
    assert stub.td_execute.restype is ctypes.c_char_p


def test_create_client_id_returns_a_plain_int(td: TDJson) -> None:
    assert td.create_client_id() == 1
    assert td.create_client_id() == 2


def test_send_forwards_client_id_and_bytes(td: TDJson, stub: StubLib) -> None:
    td.send(7, b'{"@type":"getMe"}')
    assert stub.td_send.calls == [(7, b'{"@type":"getMe"}')]


def test_receive_passes_the_timeout_as_a_c_double(td: TDJson, stub: StubLib) -> None:
    td.receive(1.5)
    (passed,) = stub.td_receive.calls[0]
    assert isinstance(passed, ctypes.c_double)
    assert passed.value == 1.5


def test_receive_returns_the_payload(td: TDJson, stub: StubLib) -> None:
    stub.received.append(b'{"@type":"updateNewMessage"}')
    assert td.receive(0.0) == b'{"@type":"updateNewMessage"}'


def test_receive_returns_none_when_idle(td: TDJson) -> None:
    assert td.receive(0.0) is None


def test_second_concurrent_receive_raises(td: TDJson, stub: StubLib) -> None:
    inside = threading.Event()
    release = threading.Event()

    def slow_receive(_timeout: Any) -> None:
        inside.set()
        release.wait(timeout=2.0)
        return None

    stub.td_receive.impl = slow_receive
    holder = threading.Thread(target=lambda: td.receive(1.0))
    holder.start()
    try:
        assert inside.wait(timeout=2.0)
        with pytest.raises(ConcurrentReceiveError, match="only one receiving thread"):
            td.receive(0.0)
    finally:
        release.set()
        holder.join(timeout=2.0)


def test_the_receive_guard_is_released_after_an_error(td: TDJson, stub: StubLib) -> None:
    def explode(_timeout: Any) -> None:
        raise RuntimeError("library blew up")

    stub.td_receive.impl = explode
    with pytest.raises(RuntimeError):
        td.receive(0.0)

    stub.td_receive.impl = lambda _timeout: b"{}"
    assert td.receive(0.0) == b"{}", "a failed receive must not wedge the lock"


def test_execute_returns_the_payload(td: TDJson) -> None:
    assert td.execute(b'{"@type":"getTextEntities"}') == b'{"@type":"ok"}'


def test_clearing_the_log_callback_passes_a_null_pointer(td: TDJson, stub: StubLib) -> None:
    td.set_log_callback(3, None)
    verbosity, callback = stub.td_set_log_message_callback.calls[-1]
    assert verbosity == 3
    assert not callback  # a NULL CFUNCTYPE is falsy


def test_log_callback_receives_decoded_text(td: TDJson, stub: StubLib) -> None:
    seen: list[tuple[int, str]] = []
    td.set_log_callback(2, lambda level, message: seen.append((level, message)))

    _verbosity, trampoline = stub.td_set_log_message_callback.calls[-1]
    trampoline(1, b"something failed\n")

    assert seen == [(1, "something failed")]


def test_log_callback_never_propagates_into_c(td: TDJson, stub: StubLib) -> None:
    def hostile(_level: int, _message: str) -> None:
        raise ValueError("handler bug")

    td.set_log_callback(2, hostile)
    _verbosity, trampoline = stub.td_set_log_message_callback.calls[-1]

    trampoline(1, b"boom")  # must not raise: unwinding into C aborts the process


def test_undecodable_log_bytes_do_not_break_the_sink(td: TDJson, stub: StubLib) -> None:
    seen: list[str] = []
    td.set_log_callback(2, lambda _level, message: seen.append(message))

    _verbosity, trampoline = stub.td_set_log_message_callback.calls[-1]
    trampoline(2, b"\xff\xfe bad bytes")

    assert seen and "bad bytes" in seen[0]


def test_forward_logs_maps_verbosity_to_logging_levels(
    td: TDJson, stub: StubLib, caplog: pytest.LogCaptureFixture
) -> None:
    td.forward_logs_to_logging(max_verbosity=4)
    _verbosity, trampoline = stub.td_set_log_message_callback.calls[-1]

    with caplog.at_level(logging.DEBUG, logger="tdpy.tdlib"):
        trampoline(1, b"an error")
        trampoline(3, b"some info")
        trampoline(9, b"very verbose")

    levels = [record.levelno for record in caplog.records]
    assert levels == [logging.ERROR, logging.INFO, logging.DEBUG]


def test_close_is_idempotent(td: TDJson, stub: StubLib) -> None:
    td.close()
    calls_after_first = len(stub.td_set_log_message_callback.calls)
    td.close()

    assert len(stub.td_set_log_message_callback.calls) == calls_after_first


def test_get_tdjson_caches_one_instance(stub: StubLib) -> None:
    reset_tdjson()
    try:
        assert get_tdjson() is get_tdjson()
    finally:
        reset_tdjson()


def test_reset_closes_the_cached_instance(stub: StubLib) -> None:
    reset_tdjson()
    instance = get_tdjson()
    reset_tdjson()

    assert instance._closed
    assert get_tdjson() is not instance
    reset_tdjson()
