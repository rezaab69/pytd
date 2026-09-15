"""What every handler gets alongside its event."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tdpy.api import types

if TYPE_CHECKING:
    from tdpy.core.client import Client
    from tdpy.fsm.context import FSMContext

__all__ = ["Context"]


@dataclass(slots=True)
class Context:
    """The dispatch context for one update.

    Attributes:
        client: The client the update arrived on, for replying.
        update: The whole typed update, even when the handler was given a
            narrower subject such as a :class:`~tdpy.api.types.Message`.
        data: Scratch space shared by middleware and handlers. Filters put
            their findings here -- :class:`~tdpy.dispatch.filters.Command`
            leaves the parsed command, :class:`~tdpy.dispatch.filters.Regex`
            leaves the match.
        state: Conversation state, present when the dispatcher has storage.
    """

    client: Client
    update: types.Update
    data: dict[str, Any] = field(default_factory=dict)
    state: FSMContext | None = None

    @property
    def fsm(self) -> FSMContext:
        """The conversation state, insisting it exists.

        Raises:
            RuntimeError: if the dispatcher was built without storage, which is
                a configuration mistake rather than a runtime condition.
        """
        if self.state is None:
            raise RuntimeError("no FSM storage configured; pass storage= to the Dispatcher")
        return self.state
