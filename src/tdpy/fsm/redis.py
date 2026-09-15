"""Conversation state that survives a restart.

Takes any object with async ``get``/``set``/``delete`` rather than importing
redis at module level: the dependency stays optional, and the storage can be
tested without a server.
"""

from __future__ import annotations

from typing import Any, Protocol

import msgspec

from tdpy.fsm.storage import StorageKey

__all__ = ["RedisLike", "RedisStorage"]

_DEFAULT_PREFIX = "tdpy:fsm"


class RedisLike(Protocol):
    """The slice of the redis asyncio client this storage uses."""

    async def get(self, name: str) -> bytes | str | None: ...

    async def set(self, name: str, value: bytes | str, ex: int | None = None) -> Any: ...

    async def delete(self, *names: str) -> Any: ...

    async def aclose(self) -> None: ...


class RedisStorage:
    """Keeps conversation state in Redis.

    Args:
        redis: A connected ``redis.asyncio`` client, or anything matching
            :class:`RedisLike`.
        prefix: Namespace for every key this storage writes.
        ttl: Seconds after which an idle conversation expires. ``None`` keeps
            it forever, which for an abandoned conversation means leaking a key
            per user -- set one unless you clear state yourself.
    """

    def __init__(
        self,
        redis: RedisLike,
        *,
        prefix: str = _DEFAULT_PREFIX,
        ttl: int | None = None,
    ) -> None:
        self.redis = redis
        self.prefix = prefix
        self.ttl = ttl
        self._encoder = msgspec.json.Encoder()
        self._decoder = msgspec.json.Decoder(dict)

    @classmethod
    def from_url(cls, url: str, **kwargs: Any) -> RedisStorage:
        """Connect with ``redis.asyncio``, which must be installed.

        Raises:
            ImportError: if the optional ``redis`` dependency is missing.
        """
        try:
            from redis.asyncio import Redis
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError(
                "RedisStorage.from_url needs the redis package: pip install 'tdpy[redis]'"
            ) from exc
        return cls(Redis.from_url(url), **kwargs)

    def _key(self, key: StorageKey, part: str) -> str:
        return f"{self.prefix}:{key}:{part}"

    async def get_state(self, key: StorageKey) -> str | None:
        raw = await self.redis.get(self._key(key, "state"))
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else raw

    async def set_state(self, key: StorageKey, state: str | None) -> None:
        name = self._key(key, "state")
        if state is None:
            await self.redis.delete(name)
        else:
            await self.redis.set(name, state, ex=self.ttl)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        raw = await self.redis.get(self._key(key, "data"))
        if raw is None:
            return {}
        if isinstance(raw, str):
            raw = raw.encode()
        result: dict[str, Any] = self._decoder.decode(raw)
        return result

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        name = self._key(key, "data")
        if not data:
            await self.redis.delete(name)
            return
        await self.redis.set(name, self._encoder.encode(data), ex=self.ttl)

    async def close(self) -> None:
        await self.redis.aclose()
