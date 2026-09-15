"""The typed client, end to end against the fake backend."""

from __future__ import annotations

import pytest

from tdpy._ffi import FakeTDJson
from tdpy.api import types
from tdpy.api.base import Int64
from tdpy.api.client import TypedClient
from tdpy.core.client import Client
from tdpy.core.errors import BadRequest

CLOSED = {
    "@type": "updateAuthorizationState",
    "authorization_state": {"@type": "authorizationStateClosed"},
}


@pytest.fixture
async def client(fake: FakeTDJson):
    fake.on("close", lambda _req: CLOSED)
    async with TypedClient(backend=fake, poll_timeout=0.05) as instance:
        yield instance


async def test_a_method_returns_a_typed_object(client: TypedClient, fake: FakeTDJson) -> None:
    # Non-nullable union fields are required, so a realistic reply carries them.
    fake.on(
        "getMe",
        {
            "@type": "user",
            "id": 42,
            "first_name": "Ada",
            "status": {"@type": "userStatusOnline"},
            "type": {"@type": "userTypeRegular"},
        },
    )

    me = await client.get_me()

    assert isinstance(me, types.User)
    assert me.first_name == "Ada"
    assert me.id == 42


async def test_the_request_is_sent_with_the_right_tag(
    client: TypedClient, fake: FakeTDJson
) -> None:
    fake.on("getChatHistory", {"@type": "messages", "total_count": 0, "messages": []})

    await client.get_chat_history(chat_id=7, limit=10)
    sent = fake.sent_of("getChatHistory")[0]

    assert sent["chat_id"] == 7
    assert sent["limit"] == 10


async def test_unset_optional_arguments_are_not_sent(client: TypedClient, fake: FakeTDJson) -> None:
    fake.on(
        "sendMessage",
        {
            "@type": "message",
            "id": 1,
            "sender_id": {"@type": "messageSenderUser"},
            "content": {"@type": "messageText", "text": {"@type": "formattedText"}},
        },
    )

    await client.send_message(
        chat_id=5,
        input_message_content=types.InputMessageText(text=types.FormattedText(text="hi")),
    )
    sent = fake.sent_of("sendMessage")[0]

    assert "reply_markup" not in sent
    assert "options" not in sent
    assert sent["input_message_content"]["@type"] == "inputMessageText"


async def test_a_union_result_decodes_to_its_variant(client: TypedClient, fake: FakeTDJson) -> None:
    fake.on(
        "getAuthorizationState",
        {"@type": "authorizationStateWaitPhoneNumber"},
    )

    state = await client.get_authorization_state()

    assert isinstance(state, types.AuthorizationStateWaitPhoneNumber)


async def test_tdlib_errors_still_raise(client: TypedClient, fake: FakeTDJson) -> None:
    fake.on("getChat", {"@type": "error", "code": 400, "message": "CHAT_NOT_FOUND"})

    with pytest.raises(BadRequest, match="CHAT_NOT_FOUND"):
        await client.get_chat(chat_id=1)


async def test_int64_survives_a_round_trip_through_the_client(
    client: TypedClient, fake: FakeTDJson
) -> None:
    fake.on(
        "getStickerSet",
        {
            "@type": "stickerSet",
            "id": "1258816259751983",
            "title": "Set",
            "sticker_type": {"@type": "stickerTypeRegular"},
        },
    )

    result = await client.get_sticker_set(set_id=Int64(1258816259751983))

    assert result.id == 1258816259751983
    assert isinstance(result.id, Int64)
    # And it went out as a string, not a lossy JSON number.
    assert fake.sent_of("getStickerSet")[0]["set_id"] == "1258816259751983"


async def test_sync_methods_need_no_running_client(fake: FakeTDJson) -> None:
    fake.on_execute(
        "getTextEntities",
        {
            "@type": "textEntities",
            "entities": [
                {
                    "@type": "textEntity",
                    "offset": 0,
                    "length": 6,
                    "type": {"@type": "textEntityTypeMention"},
                }
            ],
        },
    )
    offline = TypedClient(backend=fake)

    entities = offline.sync.get_text_entities(text="@durov")

    assert isinstance(entities, types.TextEntities)
    assert entities.entities[0].length == 6


async def test_close_keeps_the_graceful_shutdown(client: TypedClient) -> None:
    # TDLib also has a `close` function; the inherited one must win or the
    # receive loop is left running.
    assert type(client).close is Client.close


def test_top_level_exports_are_lazy() -> None:
    import subprocess
    import sys

    code = (
        "import sys, tdpy;"
        "before = 'tdpy.api.types' in sys.modules;"
        "cls = tdpy.TypedClient;"
        "print(before, 'tdpy.api.types' in sys.modules, cls.__name__)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.split()

    assert out == ["False", "True", "TypedClient"]


def test_an_unknown_top_level_name_still_raises() -> None:
    import tdpy

    with pytest.raises(AttributeError, match="Nonsense"):
        _ = tdpy.Nonsense
