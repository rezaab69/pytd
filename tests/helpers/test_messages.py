"""The send facade."""

from __future__ import annotations

import pytest

from tdpy._ffi import FakeTDJson
from tdpy.api import types
from tdpy.api.client import TypedClient
from tdpy.helpers.messages import reply_text, send_text

SENT = {
    "@type": "message",
    "id": 1,
    "chat_id": 100,
    "sender_id": {"@type": "messageSenderUser", "user_id": 7},
    "content": {"@type": "messageText", "text": {"@type": "formattedText", "text": "hi"}},
}


async def test_send_text_builds_the_content(client: TypedClient, fake: FakeTDJson) -> None:
    fake.on("sendMessage", SENT)

    result = await send_text(client, 100, "hi")
    sent = fake.sent_of("sendMessage")[0]

    assert isinstance(result, types.Message)
    assert sent["chat_id"] == 100
    assert sent["input_message_content"]["@type"] == "inputMessageText"
    assert sent["input_message_content"]["text"]["text"] == "hi"


async def test_text_is_literal_without_a_parse_mode(client: TypedClient, fake: FakeTDJson) -> None:
    # The safe default: user-supplied text must not be interpreted as markup.
    fake.on("sendMessage", SENT)

    await send_text(client, 100, "*not bold*")
    sent = fake.sent_of("sendMessage")[0]

    assert sent["input_message_content"]["text"]["text"] == "*not bold*"
    assert "entities" not in sent["input_message_content"]["text"]


async def test_a_parse_mode_is_applied(client: TypedClient, fake: FakeTDJson) -> None:
    fake.on("sendMessage", SENT)
    fake.on_execute(
        "parseTextEntities",
        {
            "@type": "formattedText",
            "text": "bold",
            "entities": [
                {
                    "@type": "textEntity",
                    "offset": 0,
                    "length": 4,
                    "type": {"@type": "textEntityTypeBold"},
                }
            ],
        },
    )

    await send_text(client, 100, "*bold*", parse_mode="markdown")
    text = fake.sent_of("sendMessage")[0]["input_message_content"]["text"]

    assert text["text"] == "bold"
    assert text["entities"][0]["length"] == 4


async def test_quiet_delivery_sets_the_option(client: TypedClient, fake: FakeTDJson) -> None:
    fake.on("sendMessage", SENT)

    await send_text(client, 100, "hi", disable_notification=True)
    sent = fake.sent_of("sendMessage")[0]

    assert sent["options"]["disable_notification"] is True


async def test_reply_targets_the_message_and_its_chat(
    client: TypedClient, fake: FakeTDJson
) -> None:
    fake.on("sendMessage", SENT)
    original = types.Message(
        id=42,
        chat_id=555,
        sender_id=types.MessageSenderUser(user_id=7),
        content=types.MessageText(text=types.FormattedText(text="ping")),
    )

    await reply_text(client, original, "pong")
    sent = fake.sent_of("sendMessage")[0]

    assert sent["chat_id"] == 555
    assert sent["reply_to"] == {"@type": "inputMessageReplyToMessage", "message_id": 42}


async def test_send_errors_propagate(client: TypedClient, fake: FakeTDJson) -> None:
    from tdpy.core.errors import BadRequest

    fake.on("sendMessage", {"@type": "error", "code": 400, "message": "CHAT_NOT_FOUND"})

    with pytest.raises(BadRequest, match="CHAT_NOT_FOUND"):
        await send_text(client, 100, "hi")
