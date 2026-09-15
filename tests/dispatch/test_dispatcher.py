"""End-to-end dispatch: ordering, middleware, isolation, FSM."""

from __future__ import annotations

import asyncio

import pytest

from tdpy._ffi import FakeTDJson
from tdpy.api import types
from tdpy.api.client import TypedClient
from tdpy.dispatch import Context, Dispatcher, Router, SkipHandler
from tdpy.dispatch.filters import Command, StateIs, Text
from tdpy.fsm import MemoryStorage, State, StatesGroup

CLOSED = {
    "@type": "updateAuthorizationState",
    "authorization_state": {"@type": "authorizationStateClosed"},
}


def new_message(text: str, *, chat_id: int = 100, user_id: int = 7) -> types.UpdateNewMessage:
    return types.UpdateNewMessage(
        message=types.Message(
            id=1,
            chat_id=chat_id,
            sender_id=types.MessageSenderUser(user_id=user_id),
            content=types.MessageText(text=types.FormattedText(text=text)),
        )
    )


@pytest.fixture
async def client(fake: FakeTDJson):
    fake.on("close", lambda _req: CLOSED)
    async with TypedClient(backend=fake, poll_timeout=0.05) as instance:
        yield instance


async def test_an_update_reaches_its_handler(client: TypedClient) -> None:
    router = Router("test")
    seen: list[str] = []

    @router.on_message(Text("hi"))
    async def greet(message: types.Message, ctx: Context) -> None:
        seen.append(message.content.text.text)

    await Dispatcher(router).feed(client, new_message("hi"))

    assert seen == ["hi"]


async def test_the_handler_is_given_the_message_not_the_update(client: TypedClient) -> None:
    router = Router("test")
    seen: list[object] = []

    @router.on_message()
    async def capture(message, ctx: Context) -> None:
        seen.append((type(message), type(ctx.update)))

    await Dispatcher(router).feed(client, new_message("hi"))

    assert seen == [(types.Message, types.UpdateNewMessage)]


async def test_only_the_first_match_runs(client: TypedClient) -> None:
    router = Router("test")
    seen: list[str] = []

    @router.on_message()
    async def first(message, ctx) -> None:
        seen.append("first")

    @router.on_message()
    async def second(message, ctx) -> None:
        seen.append("second")

    await Dispatcher(router).feed(client, new_message("hi"))

    assert seen == ["first"]


async def test_skip_handler_passes_the_event_along(client: TypedClient) -> None:
    router = Router("test")
    seen: list[str] = []

    @router.on_message()
    async def looks_and_declines(message, ctx) -> None:
        seen.append("first")
        raise SkipHandler

    @router.on_message()
    async def takes_it(message, ctx) -> None:
        seen.append("second")

    await Dispatcher(router).feed(client, new_message("hi"))

    assert seen == ["first", "second"]


async def test_filters_choose_between_handlers(client: TypedClient) -> None:
    router = Router("test")
    seen: list[str] = []

    @router.on_message(Command("start"))
    async def on_start(message, ctx) -> None:
        seen.append("start")

    @router.on_message(Text())
    async def on_text(message, ctx) -> None:
        seen.append("text")

    dispatcher = Dispatcher(router)
    await dispatcher.feed(client, new_message("/start"))
    await dispatcher.feed(client, new_message("hello"))

    assert seen == ["start", "text"]


async def test_nested_routers_are_searched(client: TypedClient) -> None:
    parent, child = Router("parent"), Router("child")
    seen: list[str] = []

    @child.on_message(Command("admin"))
    async def admin(message, ctx) -> None:
        seen.append("admin")

    parent.include(child)
    await Dispatcher(parent).feed(client, new_message("/admin"))

    assert seen == ["admin"]


async def test_an_unmatched_update_is_simply_ignored(client: TypedClient) -> None:
    router = Router("test")

    @router.on_message(Command("start"))
    async def on_start(message, ctx) -> None: ...

    dispatcher = Dispatcher(router)
    await dispatcher.feed(client, new_message("not a command"))

    assert dispatcher.handled == 0


async def test_middleware_wraps_outermost_first(client: TypedClient) -> None:
    router = Router("test")
    order: list[str] = []

    @router.middleware
    async def outer(handler, event, ctx):
        order.append("outer in")
        try:
            return await handler(event, ctx)
        finally:
            order.append("outer out")

    @router.middleware
    async def inner(handler, event, ctx):
        order.append("inner in")
        try:
            return await handler(event, ctx)
        finally:
            order.append("inner out")

    @router.on_message()
    async def handle(message, ctx) -> None:
        order.append("handler")

    await Dispatcher(router).feed(client, new_message("hi"))

    assert order == ["outer in", "inner in", "handler", "inner out", "outer out"]


async def test_a_parents_middleware_wraps_a_childs(client: TypedClient) -> None:
    parent, child = Router("parent"), Router("child")
    order: list[str] = []

    @parent.middleware
    async def from_parent(handler, event, ctx):
        order.append("parent")
        return await handler(event, ctx)

    @child.middleware
    async def from_child(handler, event, ctx):
        order.append("child")
        return await handler(event, ctx)

    @child.on_message()
    async def handle(message, ctx) -> None:
        order.append("handler")

    parent.include(child)
    await Dispatcher(parent).feed(client, new_message("hi"))

    assert order == ["parent", "child", "handler"]


async def test_middleware_can_refuse_to_call_the_handler(client: TypedClient) -> None:
    router = Router("test")
    seen: list[str] = []

    @router.middleware
    async def gate(handler, event, ctx):
        seen.append("blocked")
        return None

    @router.on_message()
    async def handle(message, ctx) -> None:
        seen.append("handler")

    await Dispatcher(router).feed(client, new_message("hi"))

    assert seen == ["blocked"]


async def test_middleware_can_put_things_in_the_context(client: TypedClient) -> None:
    router = Router("test")
    seen: list[object] = []

    @router.middleware
    async def inject(handler, event, ctx):
        ctx.data["session"] = "a-session"
        return await handler(event, ctx)

    @router.on_message()
    async def handle(message, ctx: Context) -> None:
        seen.append(ctx.data["session"])

    await Dispatcher(router).feed(client, new_message("hi"))

    assert seen == ["a-session"]


async def test_a_raising_handler_does_not_stop_dispatch(client: TypedClient) -> None:
    router = Router("test")
    seen: list[str] = []

    @router.on_message(Command("boom"))
    async def explodes(message, ctx) -> None:
        raise ValueError("handler bug")

    @router.on_message()
    async def fine(message, ctx) -> None:
        seen.append("fine")

    errors: list[BaseException] = []
    dispatcher = Dispatcher(router, on_error=lambda error, event, ctx: errors.append(error))
    await dispatcher.feed(client, new_message("/boom"))
    await dispatcher.feed(client, new_message("hello"))

    assert [type(e).__name__ for e in errors] == ["ValueError"]
    assert seen == ["fine"], "the next update must still be handled"


async def test_an_error_handler_that_itself_raises_is_survived(
    client: TypedClient, caplog: pytest.LogCaptureFixture
) -> None:
    router = Router("test")

    @router.on_message()
    async def explodes(message, ctx) -> None:
        raise ValueError("handler bug")

    def bad_error_handler(error, event, ctx):
        raise RuntimeError("error handler bug")

    dispatcher = Dispatcher(router, on_error=bad_error_handler)
    await dispatcher.feed(client, new_message("hi"))

    assert "error handler itself raised" in caplog.text


def test_workers_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        Dispatcher(Router("test"), workers=0)


async def test_updates_flow_from_the_client(client: TypedClient, fake: FakeTDJson) -> None:
    router = Router("test")
    seen: list[str] = []
    arrived = asyncio.Event()

    @router.on_message()
    async def handle(message, ctx) -> None:
        seen.append(message.content.text.text)
        arrived.set()

    async with Dispatcher(router).run(client):
        fake.push({**_as_payload(new_message("live")), "@client_id": 1}, client_id=1)
        await asyncio.wait_for(arrived.wait(), timeout=2.0)

    assert seen == ["live"]


async def test_a_single_worker_preserves_order(client: TypedClient, fake: FakeTDJson) -> None:
    # Telegram's ordering carries meaning; the default must not shuffle it.
    router = Router("test")
    seen: list[str] = []
    done = asyncio.Event()

    @router.on_message()
    async def handle(message, ctx) -> None:
        text = message.content.text.text
        await asyncio.sleep(0.02 if text == "first" else 0)
        seen.append(text)
        if len(seen) == 3:
            done.set()

    async with Dispatcher(router).run(client):
        for text in ("first", "second", "third"):
            fake.push(_as_payload(new_message(text)), client_id=1)
        await asyncio.wait_for(done.wait(), timeout=5.0)

    assert seen == ["first", "second", "third"]


async def test_stopping_drains_what_is_queued(client: TypedClient, fake: FakeTDJson) -> None:
    router = Router("test")
    seen: list[str] = []

    @router.on_message()
    async def handle(message, ctx) -> None:
        await asyncio.sleep(0.01)
        seen.append(message.content.text.text)

    dispatcher = Dispatcher(router)
    await dispatcher.start(client)
    for text in ("a", "b", "c"):
        fake.push(_as_payload(new_message(text)), client_id=1)
    await asyncio.sleep(0.05)
    await dispatcher.stop()

    assert seen == ["a", "b", "c"], "queued updates must not be dropped on shutdown"


async def test_an_unknown_update_type_is_counted_and_skipped(
    client: TypedClient, fake: FakeTDJson
) -> None:
    # A newer TDLib will send update types this build has never heard of.
    router = Router("test")
    seen: list[str] = []
    arrived = asyncio.Event()

    @router.on_update()
    async def handle(update, ctx) -> None:
        seen.append(type(update).__name__)
        arrived.set()

    dispatcher = Dispatcher(router)
    async with dispatcher.run(client):
        fake.push({"@type": "updateFromTheFuture", "whatever": 1}, client_id=1)
        fake.push(_as_payload(new_message("still works")), client_id=1)
        await asyncio.wait_for(arrived.wait(), timeout=2.0)

    assert dispatcher.undecodable == 1
    assert seen == ["UpdateNewMessage"], "one unknown update must not stop the rest"


async def test_starting_twice_is_refused(client: TypedClient) -> None:
    dispatcher = Dispatcher(Router("test"))
    await dispatcher.start(client)
    try:
        with pytest.raises(RuntimeError, match="already started"):
            await dispatcher.start(client)
    finally:
        await dispatcher.stop()


async def test_stopping_unsubscribes(client: TypedClient) -> None:
    dispatcher = Dispatcher(Router("test"))
    await dispatcher.start(client)
    await dispatcher.stop()

    assert client._observers == [], "a stopped dispatcher must not keep observing"


def _as_payload(update: types.Update) -> dict:
    from tdpy.api.base import to_builtins

    return to_builtins(update)


class Form(StatesGroup):
    name = State()
    age = State()


async def test_a_conversation_walks_through_its_states(client: TypedClient) -> None:
    router = Router("test")
    finished: list[dict] = []

    @router.on_message(Command("start"), StateIs(None))
    async def begin(message, ctx: Context) -> None:
        await ctx.fsm.set_state(Form.name)

    @router.on_message(StateIs(Form.name))
    async def take_name(message, ctx: Context) -> None:
        await ctx.fsm.update_data(name=message.content.text.text)
        await ctx.fsm.set_state(Form.age)

    @router.on_message(StateIs(Form.age))
    async def take_age(message, ctx: Context) -> None:
        data = await ctx.fsm.update_data(age=message.content.text.text)
        finished.append(data)
        await ctx.fsm.clear()

    dispatcher = Dispatcher(router, storage=MemoryStorage())
    for text in ("/start", "Ada", "36"):
        await dispatcher.feed(client, new_message(text))

    assert finished == [{"name": "Ada", "age": "36"}]


async def test_two_users_hold_separate_conversations(client: TypedClient) -> None:
    router = Router("test")

    @router.on_message(Command("start"))
    async def begin(message, ctx: Context) -> None:
        await ctx.fsm.set_state(Form.name)

    @router.on_message(StateIs(Form.name))
    async def take_name(message, ctx: Context) -> None:
        await ctx.fsm.update_data(name=message.content.text.text)

    storage = MemoryStorage()
    dispatcher = Dispatcher(router, storage=storage)
    await dispatcher.feed(client, new_message("/start", user_id=7))
    await dispatcher.feed(client, new_message("Ada", user_id=7))
    await dispatcher.feed(client, new_message("Bob", user_id=9))

    from tdpy.fsm import StorageKey

    mine = StorageKey(chat_id=100, user_id=7, client_id=client.client_id)
    theirs = StorageKey(chat_id=100, user_id=9, client_id=client.client_id)
    assert await storage.get_data(mine) == {"name": "Ada"}
    assert await storage.get_data(theirs) == {}, "an unstarted conversation stays empty"


async def test_without_storage_the_fsm_says_so(client: TypedClient) -> None:
    router = Router("test")
    errors: list[BaseException] = []

    @router.on_message()
    async def handle(message, ctx: Context) -> None:
        _ = ctx.fsm

    dispatcher = Dispatcher(router, on_error=lambda e, event, ctx: errors.append(e))
    await dispatcher.feed(client, new_message("hi"))

    assert isinstance(errors[0], RuntimeError)
    assert "storage" in str(errors[0])


async def test_state_is_none_matches_before_a_conversation_starts(
    client: TypedClient,
) -> None:
    router = Router("test")
    seen: list[str] = []

    @router.on_message(StateIs(None))
    async def fresh(message, ctx: Context) -> None:
        seen.append("fresh")
        await ctx.fsm.set_state(Form.name)

    @router.on_message(StateIs(Form.name))
    async def continuing(message, ctx: Context) -> None:
        seen.append("continuing")

    dispatcher = Dispatcher(router, storage=MemoryStorage())
    await dispatcher.feed(client, new_message("hi"))
    await dispatcher.feed(client, new_message("hi again"))

    assert seen == ["fresh", "continuing"]
