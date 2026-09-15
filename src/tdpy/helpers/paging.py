"""Async iterators over TDLib's paged results.

TDLib pages by cursor, not by offset, and it decides page sizes for itself:
the schema says outright that "the number of returned messages is chosen by
TDLib". So a page shorter than the requested limit means nothing at all, and
the only reliable end-of-history signal is an **empty** page. Stopping on a
short page -- the obvious reading -- silently truncates the history, usually
after the first batch.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from tdpy.api import functions, types
from tdpy.api.base import convert, to_builtins
from tdpy.core.client import Client

__all__ = ["MAX_BATCH", "iter_history", "iter_search"]

logger = logging.getLogger(__name__)

#: TDLib refuses a limit above this.
MAX_BATCH = 100


async def _invoke(client: Client, request: Any, result: Any) -> Any:
    payload = await client.invoke(to_builtins(request))
    return convert(payload, result)


async def iter_history(
    client: Client,
    chat_id: int,
    *,
    limit: int | None = None,
    from_message_id: int = 0,
    batch: int = MAX_BATCH,
    only_local: bool = False,
    load_chat: bool = True,
) -> AsyncIterator[types.Message]:
    """Yield a chat's messages, newest first.

    Args:
        client: A started client.
        chat_id: The chat to read.
        limit: Stop after this many messages. ``None`` reads to the beginning,
            which for a busy chat is a great many requests -- pass one unless
            you mean it.
        from_message_id: Start below this message. ``0`` starts at the newest.
        batch: Messages to ask for at a time, capped at :data:`MAX_BATCH`.
        only_local: Read only what is already cached, making this offline.
        load_chat: Fetch the chat first. TDLib will not return history for a
            chat it has not loaded, and the symptom is an empty iterator rather
            than an error.

    Yields:
        Messages in decreasing ``id`` order, which is reverse chronological.

    Example:
        >>> async for message in iter_history(client, chat_id, limit=50):
        ...     print(message.id)                               # doctest: +SKIP
    """
    if load_chat:
        await _invoke(client, functions.GetChat(chat_id=chat_id), types.Chat)

    cursor = from_message_id
    yielded = 0
    while True:
        page: types.Messages = await _invoke(
            client,
            functions.GetChatHistory(
                chat_id=chat_id,
                from_message_id=cursor,
                offset=0,
                limit=min(batch, MAX_BATCH),
                only_local=only_local,
            ),
            types.Messages,
        )
        messages = page.messages or []
        if not messages:
            # The only honest end signal. A short page is routine.
            return

        for message in messages:
            yield message
            yielded += 1
            if limit is not None and yielded >= limit:
                return

        cursor = messages[-1].id


async def iter_search(
    client: Client,
    chat_id: int,
    query: str = "",
    *,
    limit: int | None = None,
    batch: int = MAX_BATCH,
    sender_id: types.MessageSender | None = None,
    message_filter: types.SearchMessagesFilter | None = None,
) -> AsyncIterator[types.Message]:
    """Yield messages in a chat matching ``query``.

    Unlike history, search hands back its own cursor in
    ``next_from_message_id``; a zero there means there is no next page.

    Args:
        client: A started client.
        chat_id: The chat to search.
        query: Text to look for. Empty matches everything, which is how you
            enumerate by ``message_filter`` alone -- every photo, say.
        limit: Stop after this many matches.
        batch: Matches to ask for at a time, capped at :data:`MAX_BATCH`.
        sender_id: Only messages from this sender.
        message_filter: Only messages of this kind.

    Example:
        >>> photos = iter_search(                               # doctest: +SKIP
        ...     client, chat_id, message_filter=types.SearchMessagesFilterPhoto()
        ... )
    """
    cursor = 0
    yielded = 0
    while True:
        page: types.FoundChatMessages = await _invoke(
            client,
            functions.SearchChatMessages(
                chat_id=chat_id,
                query=query,
                sender_id=sender_id,
                from_message_id=cursor,
                offset=0,
                limit=min(batch, MAX_BATCH),
                filter=message_filter,
            ),
            types.FoundChatMessages,
        )
        if not page.messages:
            return

        for message in page.messages:
            yield message
            yielded += 1
            if limit is not None and yielded >= limit:
                return

        if not page.next_from_message_id:
            return
        cursor = page.next_from_message_id
