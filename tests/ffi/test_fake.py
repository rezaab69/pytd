"""The fake is load-bearing for every later phase, so it is tested like product code."""

from __future__ import annotations

import json
import threading

import pytest

from tdpy._ffi import FakeTDJson, TDJsonABI
from tdpy._ffi.fake import error
from tdpy.exceptions import TdpyError


def send(fake: FakeTDJson, client_id: int, **payload: object) -> None:
    fake.send(client_id, json.dumps(payload).encode())


def test_satisfies_the_abi_protocol(fake: FakeTDJson) -> None:
    assert isinstance(fake, TDJsonABI)


def test_client_ids_are_distinct(fake: FakeTDJson) -> None:
    ids = [fake.create_client_id() for _ in range(3)]
    assert len(set(ids)) == 3


def test_requests_are_recorded(fake: FakeTDJson) -> None:
    cid = fake.create_client_id()
    send(fake, cid, **{"@type": "getMe"})
    assert fake.sent_of("getMe") == [{"@type": "getMe"}]


def test_responder_echoes_extra_and_tags_client_id(fake: FakeTDJson) -> None:
    fake.on("getMe", {"@type": "user", "id": 42})
    cid = fake.create_client_id()
    send(fake, cid, **{"@type": "getMe", "@extra": "req-1"})

    reply = json.loads(fake.receive(1.0))
    assert reply["id"] == 42
    # Both fields are what upstream correlation depends on.
    assert reply["@extra"] == "req-1"
    assert reply["@client_id"] == cid


def test_callable_responder_sees_the_request(fake: FakeTDJson) -> None:
    fake.on("getChat", lambda req: {"@type": "chat", "id": req["chat_id"]})
    cid = fake.create_client_id()
    send(fake, cid, **{"@type": "getChat", "chat_id": 7})

    assert json.loads(fake.receive(1.0))["id"] == 7


def test_responder_returning_none_stays_silent(fake: FakeTDJson) -> None:
    fake.on("getMe", lambda _req: None)
    cid = fake.create_client_id()
    send(fake, cid, **{"@type": "getMe"})

    assert fake.receive(0.01) is None


def test_unscripted_request_gets_no_reply(fake: FakeTDJson) -> None:
    cid = fake.create_client_id()
    send(fake, cid, **{"@type": "getMe"})
    assert fake.receive(0.01) is None


def test_default_responder_catches_everything() -> None:
    fake = FakeTDJson(default_responder=error(500, "boom"))
    cid = fake.create_client_id()
    send(fake, cid, **{"@type": "anything"})

    assert json.loads(fake.receive(1.0))["message"] == "boom"
    fake.close()


def test_push_delivers_an_update(fake: FakeTDJson) -> None:
    fake.push({"@type": "updateNewMessage"}, client_id=3)
    update = json.loads(fake.receive(1.0))
    assert update == {"@type": "updateNewMessage", "@client_id": 3}


def test_receive_returns_none_on_timeout(fake: FakeTDJson) -> None:
    assert fake.receive(0.01) is None


def test_receive_preserves_order(fake: FakeTDJson) -> None:
    for n in range(3):
        fake.push({"@type": "update", "n": n})
    assert [json.loads(fake.receive(1.0))["n"] for _ in range(3)] == [0, 1, 2]


def test_execute_rejects_unscripted_methods(fake: FakeTDJson) -> None:
    result = json.loads(fake.execute(b'{"@type":"sendMessage"}'))
    assert result["@type"] == "error"
    assert result["code"] == 400


def test_execute_runs_a_scripted_sync_method(fake: FakeTDJson) -> None:
    fake.on_execute("getTextEntities", {"@type": "textEntities", "entities": []})
    result = json.loads(fake.execute(b'{"@type":"getTextEntities","@extra":"x"}'))
    assert result["@type"] == "textEntities"
    assert result["@extra"] == "x"


def test_wait_for_sent_unblocks_when_the_request_arrives(fake: FakeTDJson) -> None:
    cid = fake.create_client_id()
    threading.Timer(0.05, lambda: send(fake, cid, **{"@type": "getMe"})).start()

    assert fake.wait_for_sent("getMe", timeout=2.0)["@type"] == "getMe"


def test_wait_for_sent_reports_what_it_saw_instead(fake: FakeTDJson) -> None:
    cid = fake.create_client_id()
    send(fake, cid, **{"@type": "getChat"})

    with pytest.raises(TdpyError, match="getChat"):
        fake.wait_for_sent("getMe", timeout=0.05)


def test_close_wakes_a_blocked_receiver(fake: FakeTDJson) -> None:
    result: list[bytes | None] = []
    reader = threading.Thread(target=lambda: result.append(fake.receive(5.0)))
    reader.start()
    threading.Timer(0.05, fake.close).start()
    reader.join(timeout=2.0)

    assert not reader.is_alive(), "close() must not leave a receiver blocked"
    assert result == [None]


def test_set_log_callback_is_recorded(fake: FakeTDJson) -> None:
    def sink(level: int, message: str) -> None: ...

    fake.set_log_callback(2, sink)
    assert fake.log_verbosity == 2
    assert fake.log_callback is sink
