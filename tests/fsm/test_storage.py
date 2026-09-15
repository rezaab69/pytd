"""Both storage backends, against the same contract."""

from __future__ import annotations

from typing import Any

import pytest

from tdpy.fsm import MemoryStorage, RedisStorage, StorageKey

KEY = StorageKey(chat_id=100, user_id=7)
OTHER = StorageKey(chat_id=100, user_id=9)


class FakeRedis:
    """Enough of redis.asyncio to exercise RedisStorage without a server."""

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.expiries: dict[str, int | None] = {}
        self.closed = False

    async def get(self, name: str) -> bytes | None:
        return self.values.get(name)

    async def set(self, name: str, value: bytes | str, ex: int | None = None) -> None:
        self.values[name] = value.encode() if isinstance(value, str) else value
        self.expiries[name] = ex

    async def delete(self, *names: str) -> None:
        for name in names:
            self.values.pop(name, None)
            self.expiries.pop(name, None)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(params=["memory", "redis"])
def storage(request: pytest.FixtureRequest) -> Any:
    return MemoryStorage() if request.param == "memory" else RedisStorage(FakeRedis())


async def test_state_starts_empty(storage: Any) -> None:
    assert await storage.get_state(KEY) is None


async def test_state_round_trips(storage: Any) -> None:
    await storage.set_state(KEY, "Form:name")

    assert await storage.get_state(KEY) == "Form:name"


async def test_state_can_be_cleared(storage: Any) -> None:
    await storage.set_state(KEY, "Form:name")
    await storage.set_state(KEY, None)

    assert await storage.get_state(KEY) is None


async def test_data_starts_empty(storage: Any) -> None:
    assert await storage.get_data(KEY) == {}


async def test_data_round_trips(storage: Any) -> None:
    await storage.set_data(KEY, {"attempts": 2, "name": "Ada", "tags": ["a", "b"]})

    assert await storage.get_data(KEY) == {"attempts": 2, "name": "Ada", "tags": ["a", "b"]}


async def test_conversations_are_isolated(storage: Any) -> None:
    # Two people in the same chat are two conversations.
    await storage.set_state(KEY, "Form:name")
    await storage.set_data(KEY, {"who": "first"})

    assert await storage.get_state(OTHER) is None
    assert await storage.get_data(OTHER) == {}


async def test_emptying_data_clears_it(storage: Any) -> None:
    await storage.set_data(KEY, {"a": 1})
    await storage.set_data(KEY, {})

    assert await storage.get_data(KEY) == {}


async def test_returned_data_is_a_copy(storage: Any) -> None:
    # Mutating the result must not rewrite storage without a set_data call.
    await storage.set_data(KEY, {"tags": ["a"]})
    data = await storage.get_data(KEY)
    data["tags"].append("b")

    assert await storage.get_data(KEY) == {"tags": ["a"]}


def test_a_key_names_the_client_chat_and_user() -> None:
    assert str(StorageKey(chat_id=-100, user_id=7, client_id=2)) == "2:-100:7"


def test_keys_with_the_same_parts_are_equal() -> None:
    assert StorageKey(chat_id=1, user_id=2) == StorageKey(chat_id=1, user_id=2)
    assert StorageKey(chat_id=1, user_id=2) != StorageKey(chat_id=1, user_id=3)


async def test_redis_keys_are_namespaced() -> None:
    redis = FakeRedis()
    storage = RedisStorage(redis, prefix="app:fsm")

    await storage.set_state(KEY, "Form:name")

    assert "app:fsm:0:100:7:state" in redis.values


async def test_redis_applies_the_ttl() -> None:
    # Without one, an abandoned conversation leaks a key per user forever.
    redis = FakeRedis()
    storage = RedisStorage(redis, ttl=3600)

    await storage.set_state(KEY, "Form:name")
    await storage.set_data(KEY, {"a": 1})

    assert set(redis.expiries.values()) == {3600}


async def test_redis_close_closes_the_connection() -> None:
    redis = FakeRedis()
    await RedisStorage(redis).close()

    assert redis.closed


async def test_redis_decodes_a_string_reply() -> None:
    # Some clients are configured with decode_responses=True.
    redis = FakeRedis()
    storage = RedisStorage(redis)
    await storage.set_state(KEY, "Form:name")
    redis.values["tdpy:fsm:0:100:7:state"] = "Form:name"  # type: ignore[assignment]

    assert await storage.get_state(KEY) == "Form:name"


async def test_memory_close_forgets_everything() -> None:
    storage = MemoryStorage()
    await storage.set_state(KEY, "Form:name")
    await storage.close()

    assert await storage.get_state(KEY) is None
