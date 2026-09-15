"""Paging, and the cursor mistakes it is built to avoid."""

from __future__ import annotations

from typing import Any

from tdpy._ffi import FakeTDJson
from tdpy.api import types
from tdpy.api.client import TypedClient
from tdpy.helpers.paging import iter_history, iter_search


def message(identifier: int) -> dict[str, Any]:
    return {
        "@type": "message",
        "id": identifier,
        "chat_id": 100,
        "sender_id": {"@type": "messageSenderUser", "user_id": 7},
        "content": {
            "@type": "messageText",
            "text": {"@type": "formattedText", "text": f"m{identifier}"},
        },
    }


def history_pages(fake: FakeTDJson, pages: list[list[int]]) -> list[dict[str, Any]]:
    """Answer getChatHistory with each page in turn."""
    remaining = list(pages)
    requests: list[dict[str, Any]] = []

    def respond(request: dict[str, Any]) -> dict[str, Any]:
        requests.append(request)
        ids = remaining.pop(0) if remaining else []
        return {"@type": "messages", "total_count": len(ids), "messages": [message(i) for i in ids]}

    fake.on("getChatHistory", respond)
    fake.on(
        "getChat",
        {
            "@type": "chat",
            "id": 100,
            "type": {"@type": "chatTypePrivate"},
            "permissions": {"@type": "chatPermissions"},
            "notification_settings": {"@type": "chatNotificationSettings"},
            "available_reactions": {"@type": "chatAvailableReactionsAll"},
            "video_chat": {"@type": "videoChat"},
        },
    )
    return requests


async def test_a_single_page_is_yielded(client: TypedClient, fake: FakeTDJson) -> None:
    history_pages(fake, [[3, 2, 1], []])

    assert [m.id async for m in iter_history(client, 100)] == [3, 2, 1]


async def test_pages_are_followed_by_cursor(client: TypedClient, fake: FakeTDJson) -> None:
    requests = history_pages(fake, [[9, 8], [7, 6], []])

    assert [m.id async for m in iter_history(client, 100)] == [9, 8, 7, 6]
    # The cursor is the last id of the previous page, not an offset. The first
    # request omits it because 0 is the default, and TDLib reads absent as 0.
    assert [r.get("from_message_id", 0) for r in requests] == [0, 8, 6]


async def test_a_short_page_does_not_end_the_iteration(
    client: TypedClient, fake: FakeTDJson
) -> None:
    # TDLib picks page sizes itself, so a page smaller than the limit is
    # routine. Treating it as the end truncates history after one batch.
    history_pages(fake, [[5], [4, 3], []])

    assert [m.id async for m in iter_history(client, 100, batch=100)] == [5, 4, 3]


async def test_an_empty_page_ends_the_iteration(client: TypedClient, fake: FakeTDJson) -> None:
    history_pages(fake, [[]])

    assert [m.id async for m in iter_history(client, 100)] == []


async def test_limit_stops_early(client: TypedClient, fake: FakeTDJson) -> None:
    requests = history_pages(fake, [[9, 8, 7], [6, 5, 4], []])

    assert [m.id async for m in iter_history(client, 100, limit=4)] == [9, 8, 7, 6]
    assert len(requests) == 2, "it must not fetch a page it does not need"


async def test_batch_is_capped_at_the_tdlib_maximum(client: TypedClient, fake: FakeTDJson) -> None:
    requests = history_pages(fake, [[]])

    [m async for m in iter_history(client, 100, batch=5000)]

    assert requests[0]["limit"] == 100


async def test_starting_below_a_message(client: TypedClient, fake: FakeTDJson) -> None:
    requests = history_pages(fake, [[]])

    [m async for m in iter_history(client, 100, from_message_id=42)]

    assert requests[0]["from_message_id"] == 42


async def test_the_chat_is_loaded_first(client: TypedClient, fake: FakeTDJson) -> None:
    # TDLib returns nothing for a chat it has not loaded, and the symptom is an
    # empty iterator rather than an error.
    history_pages(fake, [[]])

    [m async for m in iter_history(client, 100)]

    assert fake.sent_of("getChat")


async def test_loading_the_chat_can_be_skipped(client: TypedClient, fake: FakeTDJson) -> None:
    history_pages(fake, [[]])

    [m async for m in iter_history(client, 100, load_chat=False)]

    assert not fake.sent_of("getChat")


# ---- search ------------------------------------------------------------


def search_pages(fake: FakeTDJson, pages: list[tuple[list[int], int]]) -> list[dict[str, Any]]:
    """Answer searchChatMessages with (ids, next_cursor) in turn."""
    remaining = list(pages)
    requests: list[dict[str, Any]] = []

    def respond(request: dict[str, Any]) -> dict[str, Any]:
        requests.append(request)
        ids, cursor = remaining.pop(0) if remaining else ([], 0)
        return {
            "@type": "foundChatMessages",
            "total_count": len(ids),
            "messages": [message(i) for i in ids],
            "next_from_message_id": cursor,
        }

    fake.on("searchChatMessages", respond)
    return requests


async def test_search_follows_the_servers_cursor(client: TypedClient, fake: FakeTDJson) -> None:
    # Search hands back its own cursor rather than making us guess.
    requests = search_pages(fake, [([9, 8], 8), ([7], 7), ([], 0)])

    assert [m.id async for m in iter_search(client, 100, "hello")] == [9, 8, 7]
    assert [r.get("from_message_id", 0) for r in requests] == [0, 8, 7]


async def test_a_zero_cursor_ends_the_search(client: TypedClient, fake: FakeTDJson) -> None:
    requests = search_pages(fake, [([9, 8], 0), ([7], 0)])

    assert [m.id async for m in iter_search(client, 100, "hello")] == [9, 8]
    assert len(requests) == 1, "a zero cursor means there is no next page"


async def test_search_passes_the_query_and_filter(client: TypedClient, fake: FakeTDJson) -> None:
    requests = search_pages(fake, [([], 0)])

    [
        m
        async for m in iter_search(
            client, 100, "receipts", message_filter=types.SearchMessagesFilterPhoto()
        )
    ]

    assert requests[0]["query"] == "receipts"
    assert requests[0]["filter"] == {"@type": "searchMessagesFilterPhoto"}


async def test_search_limit_stops_early(client: TypedClient, fake: FakeTDJson) -> None:
    requests = search_pages(fake, [([9, 8, 7], 7), ([6], 6)])

    assert [m.id async for m in iter_search(client, 100, limit=2)] == [9, 8]
    assert len(requests) == 1


async def test_an_empty_query_enumerates_by_filter(client: TypedClient, fake: FakeTDJson) -> None:
    requests = search_pages(fake, [([], 0)])

    [m async for m in iter_search(client, 100)]

    assert "query" not in requests[0], "an empty query is the default and stays off the wire"
