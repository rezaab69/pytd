"""The echo bot in examples/ must keep working as the API evolves."""

from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tdpy._ffi import FakeTDJson
from tdpy.api import types
from tdpy.api.client import TypedClient
from tdpy.dispatch import Context, Dispatcher

EXAMPLE = Path(__file__).parents[1] / "examples" / "echo_bot.py"
CLOSED = {
    "@type": "updateAuthorizationState",
    "authorization_state": {"@type": "authorizationStateClosed"},
}


def load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("echo_bot", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def created_message(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "@type": "message",
        "id": 1,
        "chat_id": request["chat_id"],
        "sender_id": {"@type": "messageSenderUser", "user_id": 1},
        "content": {"@type": "messageText", "text": {"@type": "formattedText", "text": ""}},
        "is_outgoing": True,
    }


def incoming(text: str, *, outgoing: bool = False) -> types.UpdateNewMessage:
    return types.UpdateNewMessage(
        message=types.Message(
            id=1,
            chat_id=100,
            sender_id=types.MessageSenderUser(user_id=7),
            content=types.MessageText(text=types.FormattedText(text=text)),
            is_outgoing=outgoing,
        )
    )


def sent_texts(fake: FakeTDJson) -> list[str]:
    return [r["input_message_content"]["text"]["text"] for r in fake.sent_of("sendMessage")]


@pytest.fixture
def backend() -> FakeTDJson:
    fake = FakeTDJson()
    fake.on("close", lambda _request: CLOSED)
    fake.on("sendMessage", created_message)
    return fake


@pytest.fixture
async def client(backend: FakeTDJson) -> AsyncIterator[TypedClient]:
    async with TypedClient(backend=backend, poll_timeout=0.05) as instance:
        yield instance


@pytest.fixture
def errors() -> list[BaseException]:
    return []


@pytest.fixture
def dispatcher(errors: list[BaseException]) -> Dispatcher:
    def collect(error: BaseException, event: Any, ctx: Context) -> None:
        errors.append(error)

    return Dispatcher(load_example().router, on_error=collect)


async def test_echoes_text(
    client: TypedClient, backend: FakeTDJson, dispatcher: Dispatcher, errors: list[BaseException]
) -> None:
    await dispatcher.feed(client, incoming("hello there"))

    assert errors == []
    assert sent_texts(backend) == ["hello there"]


async def test_start_explains_itself(
    client: TypedClient, backend: FakeTDJson, dispatcher: Dispatcher, errors: list[BaseException]
) -> None:
    await dispatcher.feed(client, incoming("/start"))

    assert errors == []
    assert sent_texts(backend) == ["Send me any text and I'll send it back."]


async def test_does_not_echo_itself(
    client: TypedClient, backend: FakeTDJson, dispatcher: Dispatcher, errors: list[BaseException]
) -> None:
    await dispatcher.feed(client, incoming("hello there", outgoing=True))

    assert errors == []
    assert backend.sent_of("sendMessage") == []
