"""Registration, nesting, and the order handlers are tried in."""

from __future__ import annotations

import pytest

from tdpy.api import types
from tdpy.dispatch.context import Context
from tdpy.dispatch.filters import Never, Text
from tdpy.dispatch.router import Router, subject_of


def names(router: Router) -> list[str]:
    return [handler.name for handler, _mw in router.chain()]


async def test_a_handler_is_registered_and_still_callable() -> None:
    router = Router("test")

    @router.on_message()
    async def greet(message, ctx) -> str:
        return "hi"

    assert names(router) == ["greet"]
    assert await greet(None, None) == "hi", "the decorator returns the function unchanged"


async def test_handlers_are_tried_in_registration_order() -> None:
    router = Router("test")

    @router.on_message()
    async def first(message, ctx) -> None: ...

    @router.on_message()
    async def second(message, ctx) -> None: ...

    assert names(router) == ["first", "second"]


async def test_nested_routers_come_after_their_parent() -> None:
    parent, child = Router("parent"), Router("child")

    @parent.on_message()
    async def outer(message, ctx) -> None: ...

    @child.on_message()
    async def inner(message, ctx) -> None: ...

    parent.include(child)

    assert names(parent) == ["outer", "inner"]


def test_a_router_cannot_be_included_twice() -> None:
    child = Router("child")
    Router("first").include(child)

    with pytest.raises(ValueError, match="already inside"):
        Router("second").include(child)


def test_a_router_cannot_include_itself() -> None:
    router = Router("test")

    with pytest.raises(ValueError, match="cannot include itself"):
        router.include(router)


def test_cycles_are_refused() -> None:
    # Lookup walks the tree; a cycle would loop forever.
    grandparent, parent, child = Router("a"), Router("b"), Router("c")
    grandparent.include(parent)
    parent.include(child)

    with pytest.raises(ValueError, match="cycle"):
        child.include(grandparent)


async def test_update_type_gates_the_handler(ctx: Context) -> None:
    router = Router("test")

    @router.on_message()
    async def on_message(message, ctx) -> None: ...

    handler, _ = next(iter(router.chain()))

    assert await handler.matches(ctx.update.message, ctx) is True

    other = Context(client=None, update=types.UpdateChatTitle(chat_id=1, title="t"))
    assert await handler.matches(other.update, other) is False


async def test_on_update_accepts_anything() -> None:
    router = Router("test")

    @router.on_update()
    async def anything(update, ctx) -> None: ...

    handler, _ = next(iter(router.chain()))
    ctx = Context(client=None, update=types.UpdateChatTitle(chat_id=1, title="t"))

    assert await handler.matches(ctx.update, ctx) is True


async def test_filters_are_combined_with_and(ctx: Context) -> None:
    router = Router("test")

    @router.on_message(Text(), Never())
    async def never_runs(message, ctx) -> None: ...

    handler, _ = next(iter(router.chain()))

    assert await handler.matches(ctx.update.message, ctx) is False


async def test_a_handler_may_take_only_the_event(ctx: Context) -> None:
    router = Router("test")
    seen = []

    @router.on_message()
    async def one_argument(message) -> None:
        seen.append(message)

    handler, _ = next(iter(router.chain()))
    await handler(ctx.update.message, ctx)

    assert seen == [ctx.update.message]
    assert handler.takes_context is False


def test_a_wrong_signature_is_caught_at_registration() -> None:
    # Better now than on the first matching update at three in the morning.
    router = Router("test")

    with pytest.raises(TypeError, match="must take"):

        @router.on_message()
        async def too_many(message, ctx, extra) -> None: ...


def test_varargs_handlers_are_given_the_context() -> None:
    router = Router("test")

    @router.on_message()
    async def flexible(*args) -> None: ...

    handler, _ = next(iter(router.chain()))
    assert handler.takes_context is True


def test_subject_of_unwraps_a_new_message() -> None:
    message = types.Message(
        id=1,
        chat_id=2,
        sender_id=types.MessageSenderUser(user_id=3),
        content=types.MessageText(text=types.FormattedText(text="hi")),
    )

    assert subject_of(types.UpdateNewMessage(message=message)) is message


def test_subject_of_leaves_other_updates_alone() -> None:
    update = types.UpdateChatTitle(chat_id=1, title="t")

    assert subject_of(update) is update


def test_repr_says_how_many_handlers() -> None:
    router = Router("admin")

    @router.on_message()
    async def one(message, ctx) -> None: ...

    assert repr(router) == "Router('admin', handlers=1)"


def test_a_router_names_itself_after_its_module() -> None:
    assert Router().name == __name__
