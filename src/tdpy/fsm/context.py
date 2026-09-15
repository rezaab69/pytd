"""The handle a handler uses to read and write conversation state."""

from __future__ import annotations

from typing import Any

from tdpy.fsm.state import State
from tdpy.fsm.storage import StateStorage, StorageKey

__all__ = ["FSMContext"]


class FSMContext:
    """Conversation state for one (chat, user) pair.

    Example:
        >>> await ctx.state.set_state(Form.name)            # doctest: +SKIP
        >>> await ctx.state.update_data(attempts=1)         # doctest: +SKIP
    """

    def __init__(self, storage: StateStorage, key: StorageKey) -> None:
        self.storage = storage
        self.key = key

    async def get_state(self) -> str | None:
        """The current state name, or ``None``."""
        return await self.storage.get_state(self.key)

    async def set_state(self, state: State | str | None) -> None:
        """Move to ``state``. ``None`` leaves the conversation stateless."""
        await self.storage.set_state(self.key, state.name if isinstance(state, State) else state)

    async def get_data(self) -> dict[str, Any]:
        """Everything stored for this conversation."""
        return await self.storage.get_data(self.key)

    async def set_data(self, data: dict[str, Any]) -> None:
        """Replace the stored data outright."""
        await self.storage.set_data(self.key, data)

    async def update_data(self, **values: Any) -> dict[str, Any]:
        """Merge ``values`` into the stored data and return the result."""
        data = await self.get_data()
        data.update(values)
        await self.set_data(data)
        return data

    async def clear(self) -> None:
        """Forget the state and the data. The end of a conversation."""
        await self.storage.set_state(self.key, None)
        await self.storage.set_data(self.key, {})
