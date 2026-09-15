"""Where conversation state lives between updates."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = ["MemoryStorage", "StateStorage", "StorageKey"]


@dataclass(frozen=True, slots=True)
class StorageKey:
    """Identifies one conversation.

    Keyed by chat *and* user because the same person can be mid-conversation in
    several chats at once, and several people can be mid-conversation in the
    same group. ``client_id`` separates accounts sharing a storage backend.
    """

    chat_id: int
    user_id: int
    client_id: int = 0

    def __str__(self) -> str:
        return f"{self.client_id}:{self.chat_id}:{self.user_id}"


@runtime_checkable
class StateStorage(Protocol):
    """The contract every backend implements."""

    async def get_state(self, key: StorageKey) -> str | None:
        """The current state name, or ``None`` if the conversation has none."""

    async def set_state(self, key: StorageKey, state: str | None) -> None:
        """Set the state, or clear it with ``None``."""

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        """The conversation's accumulated data. Empty dict when unset."""

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        """Replace the conversation's data."""

    async def close(self) -> None:
        """Release any connection the backend holds. Idempotent."""


class MemoryStorage:
    """Keeps state in this process, and loses it on restart.

    Right for bots whose conversations are short, and for tests. Anything that
    must survive a deploy wants :class:`~tdpy.fsm.redis.RedisStorage`.
    """

    def __init__(self) -> None:
        self._states: dict[StorageKey, str] = {}
        self._data: dict[StorageKey, dict[str, Any]] = {}

    async def get_state(self, key: StorageKey) -> str | None:
        return self._states.get(key)

    async def set_state(self, key: StorageKey, state: str | None) -> None:
        if state is None:
            self._states.pop(key, None)
        else:
            self._states[key] = state

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        # A copy, so a caller mutating the result does not quietly rewrite
        # stored state without ever calling set_data.
        return copy.deepcopy(self._data.get(key, {}))

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        if data:
            self._data[key] = copy.deepcopy(data)
        else:
            self._data.pop(key, None)

    async def close(self) -> None:
        self._states.clear()
        self._data.clear()
