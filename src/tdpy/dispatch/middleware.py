"""Onion-model middleware around handlers.

Each middleware receives the next layer and decides whether, when and how to
call it, which is what lets one wrap a handler in a timer, a database session
or a retry without the handler knowing::

    async def timed(handler, event, ctx):
        started = time.monotonic()
        try:
            return await handler(event, ctx)
        finally:
            ctx.client.logger.info("took %.3fs", time.monotonic() - started)

Registration order is outermost first, so the middleware registered first sees
the event first and sees the result last.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeAlias

__all__ = ["Middleware", "NextHandler", "wrap"]

#: The next layer in the chain: the handler, or the middleware inside this one.
NextHandler: TypeAlias = Callable[[Any, Any], Awaitable[Any]]

#: ``(next_handler, event, ctx) -> result``.
Middleware: TypeAlias = Callable[[NextHandler, Any, Any], Awaitable[Any]]


def wrap(handler: NextHandler, middlewares: Sequence[Middleware]) -> NextHandler:
    """Wrap ``handler`` in ``middlewares``, outermost first.

    Applying them in reverse builds the onion from the inside out, so the first
    registered ends up on the outside.
    """
    wrapped = handler
    for middleware in reversed(middlewares):
        wrapped = _bind(middleware, wrapped)
    return wrapped


def _bind(middleware: Middleware, inner: NextHandler) -> NextHandler:
    @functools.wraps(inner)
    async def layer(event: Any, ctx: Any) -> Any:
        return await middleware(inner, event, ctx)

    return layer
