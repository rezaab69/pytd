"""Real-binary checks. Skipped automatically when no libtdjson is installed."""

from __future__ import annotations

import json

import pytest

from tdpy._ffi import TDJson, TDJsonABI, TDJsonNotFoundError
from tdpy._ffi.tdjson import ConcurrentReceiveError, get_tdjson, reset_tdjson


@pytest.fixture(scope="module")
def td() -> TDJson:
    try:
        instance = TDJson()
    except TDJsonNotFoundError as exc:
        pytest.skip(f"no libtdjson available: {type(exc).__name__}")
    yield instance
    instance.close()


pytestmark = pytest.mark.requires_binary


def test_binding_satisfies_the_protocol(td: TDJson) -> None:
    assert isinstance(td, TDJsonABI)


def test_client_ids_are_distinct(td: TDJson) -> None:
    assert td.create_client_id() != td.create_client_id()


def test_execute_runs_a_synchronous_method(td: TDJson) -> None:
    # getTextEntities is one of the 28 methods TDLib allows off the main queue.
    raw = td.execute(json.dumps({"@type": "getTextEntities", "text": "@durov"}).encode())
    result = json.loads(raw)

    assert result["@type"] == "textEntities"
    assert result["entities"][0]["type"]["@type"] == "textEntityTypeMention"


def test_receive_result_survives_the_next_call(td: TDJson) -> None:
    # Guards the pointer-lifetime invariant: if `receive` ever returned a
    # borrowed buffer, this value would change or crash under the second call.
    td.execute(b'{"@type":"getTextEntities","text":"first"}')
    kept = td.execute(b'{"@type":"getTextEntities","text":"@second"}')
    td.execute(b'{"@type":"getTextEntities","text":"third"}')

    assert json.loads(kept)["entities"]


def test_receive_rejects_a_second_thread(td: TDJson) -> None:
    import threading

    started = threading.Event()
    failure: list[BaseException] = []

    def hold() -> None:
        started.set()
        td.receive(0.5)

    holder = threading.Thread(target=hold)
    holder.start()
    started.wait(timeout=1.0)
    try:
        td.receive(0.1)
    except ConcurrentReceiveError as exc:
        failure.append(exc)
    finally:
        holder.join(timeout=2.0)

    assert failure, "a second concurrent receive must raise, not corrupt memory"


def test_get_tdjson_is_a_singleton(td: TDJson) -> None:
    reset_tdjson()
    try:
        assert get_tdjson() is get_tdjson()
    finally:
        reset_tdjson()
