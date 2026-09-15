"""Client behaviour against the fake backend."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tdpy._ffi import FakeTDJson
from tdpy.core.client import Client
from tdpy.core.errors import (
    BadRequest,
    ClientClosedError,
    ClientStateError,
    FloodWait,
    ReceiverError,
    TDLibError,
)
from tdpy.core.retry import RetryPolicy

CLOSED = {
    "@type": "updateAuthorizationState",
    "authorization_state": {"@type": "authorizationStateClosed"},
}


@pytest.fixture
async def client(fake: FakeTDJson):
    # Answering `close` keeps teardown from waiting out the close timeout.
    fake.on("close", lambda _req: CLOSED)
    instance = Client(backend=fake, poll_timeout=0.05)
    async with instance:
        yield instance


async def test_start_allocates_a_client_id(client: Client) -> None:
    assert client.running
    assert client.client_id == 1


async def test_invoke_round_trips(client: Client, fake: FakeTDJson) -> None:
    fake.on("getMe", {"@type": "user", "id": 42, "first_name": "Ada"})

    assert (await client.invoke({"@type": "getMe"}))["first_name"] == "Ada"


async def test_invoke_stamps_a_unique_extra(client: Client, fake: FakeTDJson) -> None:
    fake.on("getMe", {"@type": "user"})
    await client.invoke({"@type": "getMe"})
    await client.invoke({"@type": "getMe"})

    extras = [r["@extra"] for r in fake.sent_of("getMe")]
    assert len(set(extras)) == 2


async def test_concurrent_requests_resolve_to_their_own_futures(
    client: Client, fake: FakeTDJson
) -> None:
    # The core correlation guarantee. Replies come back out of order on purpose.
    held: list[dict[str, Any]] = []

    def hold(request: dict[str, Any]) -> None:
        held.append(request)
        return None

    fake.on("getChat", hold)
    tasks = [asyncio.create_task(client.invoke({"@type": "getChat", "n": n})) for n in range(5)]
    while len(held) < 5:
        await asyncio.sleep(0)

    for request in reversed(held):
        fake.push({"@type": "chat", "n": request["n"], "@extra": request["@extra"]}, client_id=1)

    results = await asyncio.gather(*tasks)
    assert [r["n"] for r in results] == [0, 1, 2, 3, 4]


async def test_tdlib_errors_become_exceptions(client: Client, fake: FakeTDJson) -> None:
    fake.on("getChat", {"@type": "error", "code": 400, "message": "CHAT_NOT_FOUND"})

    with pytest.raises(BadRequest, match="CHAT_NOT_FOUND") as excinfo:
        await client.invoke({"@type": "getChat", "chat_id": 1})

    assert excinfo.value.request is not None
    assert excinfo.value.request["@type"] == "getChat"


async def test_invoke_can_time_out(client: Client, fake: FakeTDJson) -> None:
    fake.on("getChat", lambda _req: None)

    with pytest.raises(asyncio.TimeoutError):
        await client.invoke({"@type": "getChat"}, timeout=0.05)


async def test_a_late_response_never_leaks_into_updates(client: Client, fake: FakeTDJson) -> None:
    fake.on("getChat", lambda _req: None)
    with pytest.raises(asyncio.TimeoutError):
        await client.invoke({"@type": "getChat"}, timeout=0.05)

    stale = fake.sent_of("getChat")[0]["@extra"]
    fake.push({"@type": "chat", "@extra": stale}, client_id=1)
    fake.push({"@type": "updateNewMessage"}, client_id=1)

    seen = [update async for update in _take(client, 1)]
    assert [u["@type"] for u in seen] == ["updateNewMessage"]


async def test_updates_reach_the_stream(client: Client, fake: FakeTDJson) -> None:
    fake.push({"@type": "updateNewMessage", "n": 1}, client_id=1)
    fake.push({"@type": "updateNewMessage", "n": 2}, client_id=1)

    seen = [update async for update in _take(client, 2)]
    assert [u["n"] for u in seen] == [1, 2]


async def test_execute_works_before_start(fake: FakeTDJson) -> None:
    fake.on_execute("getTextEntities", {"@type": "textEntities", "entities": []})

    assert Client(backend=fake).execute({"@type": "getTextEntities"})["@type"] == "textEntities"


async def test_execute_raises_on_error(fake: FakeTDJson) -> None:
    with pytest.raises(TDLibError, match="can't be executed"):
        Client(backend=fake).execute({"@type": "sendMessage"})


async def test_send_nowait_does_not_stamp_extra(client: Client, fake: FakeTDJson) -> None:
    client.send_nowait({"@type": "setLogVerbosityLevel", "new_verbosity_level": 1})

    assert "@extra" not in fake.sent_of("setLogVerbosityLevel")[0]


async def _take(client: Client, count: int):
    """Yield the first ``count`` updates, then stop."""
    taken = 0
    async for update in client.updates():
        yield update
        taken += 1
        if taken >= count:
            return


async def test_close_waits_for_tdlib_to_confirm(fake: FakeTDJson) -> None:
    fake.on("close", lambda _req: CLOSED)
    client = await Client(backend=fake, poll_timeout=0.05).start()

    await client.close(timeout=2.0)

    assert not client.running
    assert fake.sent_of("close"), "close must actually be sent so the db is flushed"


async def test_close_gives_up_rather_than_hanging(fake: FakeTDJson) -> None:
    fake.on("close", lambda _req: None)  # TDLib never confirms
    client = await Client(backend=fake, poll_timeout=0.05).start()

    await client.close(timeout=0.05)

    assert not client.running


async def test_close_is_idempotent(fake: FakeTDJson) -> None:
    fake.on("close", lambda _req: CLOSED)
    client = await Client(backend=fake, poll_timeout=0.05).start()

    await client.close(timeout=2.0)
    await client.close(timeout=2.0)

    assert len(fake.sent_of("close")) == 1


async def test_closing_fails_requests_still_in_flight(fake: FakeTDJson) -> None:
    fake.on("close", lambda _req: CLOSED)
    fake.on("getChat", lambda _req: None)
    client = await Client(backend=fake, poll_timeout=0.05).start()
    pending = asyncio.create_task(client.invoke({"@type": "getChat"}))
    await _wait_sent(fake, "getChat")

    await client.close(timeout=2.0)

    with pytest.raises(ClientClosedError):
        await pending


async def test_closing_ends_the_update_stream(fake: FakeTDJson) -> None:
    fake.on("close", lambda _req: CLOSED)
    client = await Client(backend=fake, poll_timeout=0.05).start()
    drained = asyncio.create_task(_drain(client))
    await asyncio.sleep(0)

    await client.close(timeout=2.0)

    # The stream ends, but not before delivering the authorization update that
    # announced the close -- that is a real update and callers may act on it.
    seen = await asyncio.wait_for(drained, timeout=2.0)
    assert [u["@type"] for u in seen] == ["updateAuthorizationState"]


async def test_a_dead_receiver_fails_pending_requests(fake: FakeTDJson) -> None:
    # The alternative is an await that never returns, which is the worst
    # possible failure mode for a network client.
    fake.on("getChat", lambda _req: None)
    client = await Client(backend=fake, poll_timeout=0.05).start()
    pending = asyncio.create_task(client.invoke({"@type": "getChat"}))
    await _wait_sent(fake, "getChat")

    def explode(_timeout: float) -> bytes | None:
        raise RuntimeError("tdjson exploded")

    fake.receive = explode  # type: ignore[method-assign]

    with pytest.raises(ReceiverError):
        await asyncio.wait_for(pending, timeout=3.0)
    assert not client.running


async def test_a_dead_receiver_raises_out_of_the_update_stream(fake: FakeTDJson) -> None:
    client = await Client(backend=fake, poll_timeout=0.05).start()
    drained = asyncio.create_task(_drain(client))
    await asyncio.sleep(0)

    def explode(_timeout: float) -> bytes | None:
        raise RuntimeError("tdjson exploded")

    fake.receive = explode  # type: ignore[method-assign]

    with pytest.raises(ReceiverError):
        await asyncio.wait_for(drained, timeout=3.0)


async def _drain(client: Client) -> list[dict[str, Any]]:
    return [update async for update in client.updates()]


async def _wait_sent(fake: FakeTDJson, method: str, timeout: float = 2.0) -> dict[str, Any]:
    """Await a request reaching the backend without blocking the event loop."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        matches = fake.sent_of(method)
        if matches:
            return matches[0]
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"no {method!r} request within {timeout}s")
        await asyncio.sleep(0.005)


async def test_no_retry_by_default(client: Client, fake: FakeTDJson) -> None:
    # Most TDLib methods are not idempotent; silently repeating sendMessage
    # would post twice. Opting in has to be explicit.
    fake.on("sendMessage", {"@type": "error", "code": 429, "message": "FLOOD_WAIT_1"})

    with pytest.raises(FloodWait):
        await client.invoke({"@type": "sendMessage"})

    assert len(fake.sent_of("sendMessage")) == 1


async def test_retry_policy_repeats_until_it_succeeds(client: Client, fake: FakeTDJson) -> None:
    attempts = 0

    def flaky(_req: dict[str, Any]) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return {"@type": "error", "code": 500, "message": "INTERNAL"}
        return {"@type": "ok"}

    fake.on("getChat", flaky)
    policy = RetryPolicy(max_attempts=3, base_backoff=0.001, jitter=0.0)

    assert (await client.invoke({"@type": "getChat"}, retry=policy))["@type"] == "ok"
    assert attempts == 3


async def test_retry_gives_up_and_raises_the_last_error(client: Client, fake: FakeTDJson) -> None:
    fake.on("getChat", {"@type": "error", "code": 500, "message": "INTERNAL"})
    policy = RetryPolicy(max_attempts=2, base_backoff=0.001, jitter=0.0)

    with pytest.raises(TDLibError, match="INTERNAL"):
        await client.invoke({"@type": "getChat"}, retry=policy)

    assert len(fake.sent_of("getChat")) == 2


async def test_each_retry_uses_a_fresh_extra(client: Client, fake: FakeTDJson) -> None:
    fake.on("getChat", {"@type": "error", "code": 500, "message": "INTERNAL"})
    policy = RetryPolicy(max_attempts=3, base_backoff=0.001, jitter=0.0)

    with pytest.raises(TDLibError):
        await client.invoke({"@type": "getChat"}, retry=policy)

    extras = [r["@extra"] for r in fake.sent_of("getChat")]
    assert len(set(extras)) == 3, "reusing an extra would cross-wire the responses"


async def test_a_bounded_queue_drops_the_oldest_updates(fake: FakeTDJson) -> None:
    fake.on("close", lambda _req: CLOSED)
    client = await Client(backend=fake, poll_timeout=0.05, update_queue_size=2).start()
    try:
        for n in range(5):
            fake.push({"@type": "updateNewMessage", "n": n}, client_id=1)
        while client.dropped_updates < 3:
            await asyncio.sleep(0.01)

        seen = [update async for update in _take(client, 2)]
        assert [u["n"] for u in seen] == [3, 4], "the newest updates are the ones kept"
    finally:
        await client.close(timeout=2.0)


async def test_unbounded_is_the_default(client: Client, fake: FakeTDJson) -> None:
    for n in range(50):
        fake.push({"@type": "updateNewMessage", "n": n}, client_id=1)

    seen = [update async for update in _take(client, 50)]
    assert len(seen) == 50
    assert client.dropped_updates == 0


async def test_using_a_client_before_start_is_refused(fake: FakeTDJson) -> None:
    client = Client(backend=fake)

    with pytest.raises(ClientStateError):
        await client.invoke({"@type": "getMe"})
    with pytest.raises(ClientStateError):
        client.client_id  # noqa: B018


async def test_a_closed_client_cannot_be_restarted(fake: FakeTDJson) -> None:
    fake.on("close", lambda _req: CLOSED)
    client = await Client(backend=fake, poll_timeout=0.05).start()
    await client.close(timeout=2.0)

    with pytest.raises(ClientStateError, match="cannot be restarted"):
        await client.start()


async def test_double_start_is_refused(client: Client) -> None:
    with pytest.raises(ClientStateError, match="already started"):
        await client.start()


async def test_close_does_not_stall_the_event_loop(fake: FakeTDJson) -> None:
    # Stopping the receiver joins a thread that may be parked inside
    # td_receive. If that join happened inline, every other task would freeze.
    fake.on("close", lambda _req: CLOSED)
    client = await Client(backend=fake, poll_timeout=0.5).start()
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beating = asyncio.create_task(ticker())
    try:
        await client.close(timeout=2.0)
        assert ticks >= 2, f"event loop was starved during close (ticks={ticks})"
    finally:
        beating.cancel()


async def test_execute_reports_a_silent_backend(fake: FakeTDJson) -> None:
    fake.on_execute("getLogTags", lambda _req: None)

    with pytest.raises(TDLibError, match="returned nothing"):
        Client(backend=fake).execute({"@type": "getLogTags"})


async def test_requests_after_a_receiver_death_fail_fast(fake: FakeTDJson) -> None:
    client = await Client(backend=fake, poll_timeout=0.05).start()

    def explode(_timeout: float) -> bytes | None:
        raise RuntimeError("tdjson exploded")

    fake.receive = explode  # type: ignore[method-assign]
    while client.running:
        await asyncio.sleep(0.01)

    with pytest.raises(ReceiverError):
        await client.invoke({"@type": "getMe"})


async def test_observers_see_updates_without_consuming_them(
    client: Client, fake: FakeTDJson
) -> None:
    seen: list[str] = []
    client.subscribe(lambda update: seen.append(update["@type"]))

    fake.push({"@type": "updateNewMessage"}, client_id=1)
    streamed = [update async for update in _take(client, 1)]

    assert seen == ["updateNewMessage"]
    assert [u["@type"] for u in streamed] == ["updateNewMessage"]


async def test_observers_do_not_see_responses(client: Client, fake: FakeTDJson) -> None:
    seen: list[str] = []
    client.subscribe(lambda update: seen.append(update["@type"]))
    fake.on("getMe", {"@type": "user"})

    await client.invoke({"@type": "getMe"})

    assert seen == [], "a correlated response is not an update"


async def test_unsubscribe_stops_delivery(client: Client, fake: FakeTDJson) -> None:
    seen: list[str] = []
    unsubscribe = client.subscribe(lambda update: seen.append(update["@type"]))

    fake.push({"@type": "updateOption"}, client_id=1)
    await _take_one(client)
    unsubscribe()
    fake.push({"@type": "updateNewMessage"}, client_id=1)
    await _take_one(client)

    assert seen == ["updateOption"]


async def test_unsubscribing_twice_is_harmless(client: Client) -> None:
    unsubscribe = client.subscribe(lambda _update: None)
    unsubscribe()
    unsubscribe()


async def test_a_raising_observer_does_not_break_the_stream(
    client: Client, fake: FakeTDJson
) -> None:
    def hostile(_update: dict[str, Any]) -> None:
        raise ValueError("observer bug")

    seen: list[str] = []
    client.subscribe(hostile)
    client.subscribe(lambda update: seen.append(update["@type"]))

    fake.push({"@type": "updateOption"}, client_id=1)
    streamed = [update async for update in _take(client, 1)]

    assert seen == ["updateOption"], "one bad observer must not starve the others"
    assert streamed[0]["@type"] == "updateOption"


async def _take_one(client: Client) -> dict[str, Any]:
    async for update in client.updates():
        return update
    raise AssertionError("stream ended")
