"""The receive thread: routing, lifecycle, and how it dies."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from tdpy._ffi import FakeTDJson
from tdpy.core.errors import ReceiverError
from tdpy.core.receiver import Receiver, get_receiver


class RecordingSubscriber:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.failure: BaseException | None = None
        self.got = threading.Event()
        self.died = threading.Event()

    def on_payload(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)
        self.got.set()

    def on_receiver_failure(self, error: BaseException) -> None:
        self.failure = error
        self.died.set()


@pytest.fixture
def receiver(fake: FakeTDJson) -> Receiver:
    instance = Receiver(fake, poll_timeout=0.05)
    yield instance
    instance.stop()


def test_thread_starts_on_the_first_registration(receiver: Receiver) -> None:
    assert not receiver.running
    receiver.register(1, RecordingSubscriber())
    assert receiver.running


def test_thread_stops_when_the_last_subscriber_leaves(receiver: Receiver) -> None:
    receiver.register(1, RecordingSubscriber())
    receiver.register(2, RecordingSubscriber())

    receiver.unregister(1)
    assert receiver.running, "one subscriber left, so the thread must stay up"

    receiver.unregister(2)
    assert not receiver.running


def test_payloads_are_routed_by_client_id(receiver: Receiver, fake: FakeTDJson) -> None:
    first, second = RecordingSubscriber(), RecordingSubscriber()
    receiver.register(1, first)
    receiver.register(2, second)

    fake.push({"@type": "updateNewMessage"}, client_id=2)

    assert second.got.wait(timeout=2.0)
    assert not first.payloads
    assert second.payloads[0]["@type"] == "updateNewMessage"


def test_untagged_payload_goes_to_the_only_subscriber(receiver: Receiver, fake: FakeTDJson) -> None:
    only = RecordingSubscriber()
    receiver.register(1, only)

    fake.push({"@type": "updateOption"})

    assert only.got.wait(timeout=2.0)


def test_payload_for_an_unknown_client_is_dropped(receiver: Receiver, fake: FakeTDJson) -> None:
    subscriber = RecordingSubscriber()
    receiver.register(1, subscriber)

    fake.push({"@type": "updateNewMessage"}, client_id=99)
    fake.push({"@type": "updateOption"}, client_id=1)

    assert subscriber.got.wait(timeout=2.0)
    assert [p["@type"] for p in subscriber.payloads] == ["updateOption"]


def test_undecodable_payload_does_not_kill_the_loop(receiver: Receiver, fake: FakeTDJson) -> None:
    subscriber = RecordingSubscriber()
    receiver.register(1, subscriber)

    with fake._cond:
        fake._outbox.append(b"{not json")
        fake._cond.notify_all()
    fake.push({"@type": "updateOption"}, client_id=1)

    assert subscriber.got.wait(timeout=2.0)
    assert receiver.running


def test_a_raising_subscriber_does_not_kill_the_loop(receiver: Receiver, fake: FakeTDJson) -> None:
    class Hostile(RecordingSubscriber):
        def on_payload(self, payload: dict[str, Any]) -> None:
            super().on_payload(payload)
            raise ValueError("handler bug")

    hostile = Hostile()
    receiver.register(1, hostile)

    fake.push({"@type": "updateOption"}, client_id=1)
    assert hostile.got.wait(timeout=2.0)

    hostile.got.clear()
    fake.push({"@type": "updateOption"}, client_id=1)
    assert hostile.got.wait(timeout=2.0), "the loop must survive a bad handler"


def test_double_registration_is_rejected(receiver: Receiver) -> None:
    receiver.register(1, RecordingSubscriber())

    with pytest.raises(ReceiverError, match="already registered"):
        receiver.register(1, RecordingSubscriber())


def test_get_receiver_shares_one_thread_per_backend(fake: FakeTDJson) -> None:
    first = get_receiver(fake)
    try:
        assert get_receiver(fake) is first
    finally:
        first.stop()


def test_a_stopped_receiver_is_replaced_next_time(fake: FakeTDJson) -> None:
    first = get_receiver(fake)
    first.register(1, RecordingSubscriber())
    first.unregister(1)

    second = get_receiver(fake)
    try:
        assert second is not first, "a dead receiver must not be handed out again"
    finally:
        second.stop()


def test_a_dying_loop_notifies_every_subscriber(fake: FakeTDJson) -> None:
    # The whole point: if the loop dies, callers must be told rather than
    # waiting forever for a response that can never arrive.
    boom = RuntimeError("tdjson exploded")

    def explode(_timeout: float) -> bytes | None:
        raise boom

    receiver = Receiver(fake, poll_timeout=0.05)
    first, second = RecordingSubscriber(), RecordingSubscriber()
    receiver.register(1, first)
    receiver.register(2, second)
    fake.receive = explode  # type: ignore[method-assign]

    assert first.died.wait(timeout=2.0)
    assert second.died.wait(timeout=2.0)
    assert first.failure is boom
    assert receiver.failure is boom


def test_registering_on_a_failed_receiver_is_refused(fake: FakeTDJson) -> None:
    def explode(_timeout: float) -> bytes | None:
        raise RuntimeError("tdjson exploded")

    receiver = Receiver(fake, poll_timeout=0.05)
    subscriber = RecordingSubscriber()
    receiver.register(1, subscriber)
    fake.receive = explode  # type: ignore[method-assign]
    assert subscriber.died.wait(timeout=2.0)

    with pytest.raises(ReceiverError, match="already failed"):
        receiver.register(2, RecordingSubscriber())


def test_stop_is_idempotent(receiver: Receiver) -> None:
    receiver.register(1, RecordingSubscriber())
    receiver.stop()
    receiver.stop()

    assert not receiver.running
