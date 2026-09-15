"""A client whose methods are typed."""

from __future__ import annotations

import functools
from typing import Any

from tdpy.api.base import TDObject, convert, to_builtins
from tdpy.api.methods import Methods, SyncMethods
from tdpy.core.client import Client

__all__ = ["SyncApi", "TypedClient"]


class SyncApi(SyncMethods):
    """The 28 TDLib functions that run in the calling thread.

    Reached as :attr:`TypedClient.sync`. They need no request queue, no event
    loop and no started client, so they work before -- and without -- start-up.
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    def _execute(self, request: TDObject, result: Any) -> Any:
        payload = self._client.execute(to_builtins(request))
        return convert(payload, result)


class TypedClient(Client, Methods):  # type: ignore[misc]  # `close`, see below
    """A :class:`~tdpy.core.client.Client` with all 1022 TDLib functions attached.

    Example:
        >>> async with TypedClient() as client:                 # doctest: +SKIP
        ...     me = await client.get_me()
        ...     print(me.first_name)

    Results come back as the generated structs rather than dicts, so
    ``me.first_name`` is checked and completed by the type checker, and a
    64-bit value such as a custom emoji id survives as an
    :class:`~tdpy.api.base.Int64` instead of quietly losing precision. (Chat,
    user and message ids are int53 and arrive as plain ints.)

    Every method here is a coroutine. The 28 functions TDLib can also run
    synchronously live on :attr:`sync` rather than being mixed in beside their
    async siblings, so that ``await client.<anything>`` is always correct --
    mixing the two would make a handful of calls fail on ``await`` for no
    reason the caller could see.

    One name collides: TDLib has its own ``close`` function, so the generated
    ``Methods.close`` meets :meth:`Client.close`. The MRO puts ``Client``
    first, which is the safe outcome -- the inherited version also waits for
    ``authorizationStateClosed`` and detaches from the receive loop, where the
    generated one would return an ``Ok`` and leave the receiver running.
    """

    @functools.cached_property
    def sync(self) -> SyncApi:
        """TDLib's synchronously-callable functions, typed."""
        return SyncApi(self)

    async def _call(self, request: TDObject, result: Any) -> Any:
        """Send a generated request object and decode the reply as ``result``."""
        payload = await self.invoke(to_builtins(request))
        return convert(payload, result)
