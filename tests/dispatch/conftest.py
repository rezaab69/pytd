from __future__ import annotations

from typing import Any

import pytest

from tdpy.api import types
from tdpy.dispatch.context import Context


def message(
    text: str | None = None,
    *,
    chat_id: int = 100,
    user_id: int | None = 7,
    outgoing: bool = False,
    content: Any = None,
) -> types.Message:
    """A message with just enough filled in for filters to look at."""
    if content is None:
        content = (
            types.MessageText(text=types.FormattedText(text=text))
            if text is not None
            else types.MessagePhoto(photo=types.Photo(), caption=types.FormattedText())
        )
    sender: types.MessageSender = (
        types.MessageSenderUser(user_id=user_id)
        if user_id is not None
        else types.MessageSenderChat(chat_id=chat_id)
    )
    return types.Message(
        id=1, chat_id=chat_id, sender_id=sender, content=content, is_outgoing=outgoing
    )


@pytest.fixture
def make_message():
    """Factory for messages with just enough filled in for filters."""
    return message


@pytest.fixture
def ctx() -> Context:
    update = types.UpdateNewMessage(message=message("hi"))
    return Context(client=None, update=update)  # type: ignore[arg-type]
