"""Static assertions, checked by mypy rather than run by pytest.

These guard the promises a typed library makes to its users. They caught
`async with TypedClient()` narrowing to `Client`, which silently cost every
consumer all 1022 generated methods -- a failure invisible at runtime and
invisible to tests, because the code works fine. Only a type checker sees it.

Nothing here executes; pytest does not collect it (no `test_` prefix) and mypy
is pointed at it from pyproject.toml.
"""

from __future__ import annotations

from tdpy import Client, TypedClient
from tdpy.api import types
from tdpy.api.base import Int64
from tdpy.dispatch import Context, Dispatcher, Router
from tdpy.fsm import MemoryStorage, State, StatesGroup


async def context_manager_keeps_the_subclass() -> None:
    async with TypedClient() as client:
        # Would fail if __aenter__ were annotated as returning Client.
        narrowed: TypedClient = client
        await narrowed.get_me()


async def start_keeps_the_subclass() -> None:
    client: TypedClient = await TypedClient().start()
    await client.get_me()

    plain: Client = await Client().start()
    await plain.invoke({"@type": "getMe"})


async def generated_methods_are_typed() -> None:
    async with TypedClient() as client:
        me: types.User = await client.get_me()
        first_name: str = me.first_name
        del first_name

        # A union result narrows to its variants.
        state: types.AuthorizationState = await client.get_authorization_state()
        if isinstance(state, types.AuthorizationStateWaitPassword):
            hint: str = state.password_hint
            del hint


async def sync_methods_are_not_coroutines() -> None:
    client = TypedClient()
    # No await: the 28 synchronous functions return their result directly.
    entities: types.TextEntities = client.sync.get_text_entities(text="@durov")
    del entities


def int64_is_an_int() -> None:
    identifier: Int64 = Int64(5789154169261340690)
    plain: int = identifier
    del plain


async def handlers_receive_typed_events() -> None:
    router = Router("typing-checks")

    @router.on_message()
    async def handle(message: types.Message, ctx: Context) -> None:
        chat_id: int = message.chat_id
        client: Client = ctx.client
        update: types.Update = ctx.update
        del chat_id, client, update

    dispatcher: Dispatcher = Dispatcher(router, storage=MemoryStorage())
    del dispatcher


class Form(StatesGroup):
    name = State()


def states_expose_their_names() -> None:
    name: str = Form.name.name
    del name
