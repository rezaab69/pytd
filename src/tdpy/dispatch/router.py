"""Where handlers are registered and looked up.

Routers nest, which is how a larger application splits by feature without one
module importing another's handlers::

    root = Router()
    root.include(admin.router)
    root.include(payments.router)

Within a router, handlers are tried in registration order and **the first match
wins**. A handler that wants the next one to get a look raises
:class:`SkipHandler`. Making that explicit beats running every match, where
adding a broad handler silently changes what the narrow ones do.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any, TypeAlias

from tdpy.api import types
from tdpy.dispatch.context import Context
from tdpy.dispatch.filters import Filter, FilterLike, all_of
from tdpy.dispatch.middleware import Middleware
from tdpy.exceptions import TdpyError

__all__ = ["Handler", "HandlerFunction", "Router", "SkipHandler", "subject_of"]

#: ``(event, ctx) -> None``. A handler taking only ``event`` is also accepted.
HandlerFunction: TypeAlias = Callable[..., Awaitable[Any]]


class SkipHandler(TdpyError):
    """Raised by a handler to hand the event to the next one that matches."""


#: Updates whose interesting part is nested one level down. Anything absent
#: here hands the handler the whole update, which is the honest default: most
#: updates are a bag of fields with no single subject.
_SUBJECTS: dict[type, str] = {
    types.UpdateNewMessage: "message",
    types.UpdateMessageSendSucceeded: "message",
    types.UpdateMessageSendFailed: "message",
    types.UpdateNewChatJoinRequest: "request",
    types.UpdateNewChat: "chat",
}


def subject_of(update: types.Update) -> Any:
    """The part of ``update`` a handler actually wants.

    For ``updateNewMessage`` that is the ``Message``; for most updates it is
    the update itself.
    """
    attribute = _SUBJECTS.get(type(update))
    return getattr(update, attribute) if attribute else update


@dataclass(frozen=True, slots=True)
class Handler:
    """One registered handler and the conditions it runs under."""

    function: HandlerFunction
    update_type: type | tuple[type, ...] | None
    filters: Filter | None
    takes_context: bool
    name: str

    @property
    def wants_subject(self) -> bool:
        """Whether this handler is given the update's subject or the update.

        A handler registered for a specific type asked for that type, so it
        gets its subject -- a ``Message`` for ``updateNewMessage``. A handler
        registered for *any* update gets the update itself, which is the only
        thing it could sensibly mean.
        """
        return self.update_type is not None

    async def matches(self, event: Any, ctx: Context) -> bool:
        if self.update_type is not None and not isinstance(ctx.update, self.update_type):
            return False
        if self.filters is None:
            return True
        return await self.filters.check(event, ctx)

    async def __call__(self, event: Any, ctx: Context) -> Any:
        if self.takes_context:
            return await self.function(event, ctx)
        return await self.function(event)


def _takes_context(function: HandlerFunction) -> bool:
    """Whether a handler wants the context as a second argument.

    Decided once at registration rather than per update, and only for the one-
    and two-argument shapes -- anything else is a signature mistake worth
    reporting now instead of on the first matching update at three in the
    morning.
    """
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):  # pragma: no cover - exotic callables
        return True
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD, parameter.VAR_POSITIONAL)
    ]
    if any(parameter.kind is parameter.VAR_POSITIONAL for parameter in positional):
        return True
    if len(positional) in (1, 2):
        return len(positional) == 2
    raise TypeError(
        f"handler {getattr(function, '__name__', function)!r} must take (event) or "
        f"(event, ctx); it takes {len(positional)} positional arguments"
    )


class Router:
    """A group of handlers, optionally nested inside another.

    Args:
        name: Shown in logs and errors. Defaults to the module it was created
            in, which is usually the useful answer.
    """

    def __init__(self, name: str | None = None) -> None:
        self.name = name or _calling_module()
        self.handlers: list[Handler] = []
        self.middlewares: list[Middleware] = []
        self.children: list[Router] = []
        self.parent: Router | None = None

    def __repr__(self) -> str:
        return f"Router({self.name!r}, handlers={len(self.handlers)})"

    # ---- registration --------------------------------------------------

    def on(
        self,
        update_type: type | tuple[type, ...] | None = None,
        *filters: FilterLike,
    ) -> Callable[[HandlerFunction], HandlerFunction]:
        """Register a handler for ``update_type``.

        Args:
            update_type: A generated update class, a tuple of them, or ``None``
                for every update.
            filters: Extra conditions, combined with ``and``.

        Returns:
            The undecorated function, so handlers stay directly callable in
            tests.
        """

        def decorate(function: HandlerFunction) -> HandlerFunction:
            self.handlers.append(
                Handler(
                    function=function,
                    update_type=update_type,
                    filters=all_of(filters),
                    takes_context=_takes_context(function),
                    name=getattr(function, "__name__", repr(function)),
                )
            )
            return function

        return decorate

    def on_message(self, *filters: FilterLike) -> Callable[[HandlerFunction], HandlerFunction]:
        """Handle new messages. Handlers receive a :class:`~tdpy.api.types.Message`."""
        return self.on(types.UpdateNewMessage, *filters)

    def on_edited_message(
        self, *filters: FilterLike
    ) -> Callable[[HandlerFunction], HandlerFunction]:
        """Handle message edits. Handlers receive the update itself.

        TDLib reports an edit as ``updateMessageEdited`` carrying only the chat
        and message ids -- fetching the new text is the handler's job.
        """
        return self.on(types.UpdateMessageEdited, *filters)

    def on_callback_query(
        self, *filters: FilterLike
    ) -> Callable[[HandlerFunction], HandlerFunction]:
        """Handle inline keyboard presses."""
        return self.on(types.UpdateNewCallbackQuery, *filters)

    def on_inline_query(self, *filters: FilterLike) -> Callable[[HandlerFunction], HandlerFunction]:
        """Handle inline queries."""
        return self.on(types.UpdateNewInlineQuery, *filters)

    def on_chat_join_request(
        self, *filters: FilterLike
    ) -> Callable[[HandlerFunction], HandlerFunction]:
        """Handle requests to join a chat."""
        return self.on(types.UpdateNewChatJoinRequest, *filters)

    def on_update(self, *filters: FilterLike) -> Callable[[HandlerFunction], HandlerFunction]:
        """Handle every update, whatever its type.

        These handlers receive the whole update rather than a subject, since
        there is no single subject that every update type has.
        """
        return self.on(None, *filters)

    def middleware(self, function: Middleware) -> Middleware:
        """Register middleware around this router's handlers and its children's."""
        self.middlewares.append(function)
        return function

    def include(self, router: Router) -> Router:
        """Nest ``router`` inside this one.

        Raises:
            ValueError: if the router already has a parent, or if the nesting
                would form a cycle -- both would make lookup loop forever.
        """
        if router is self:
            raise ValueError("a router cannot include itself")
        if router.parent is not None:
            raise ValueError(f"{router!r} is already inside {router.parent!r}")
        for ancestor in self._ancestors():
            if ancestor is router:
                raise ValueError(f"including {router!r} would form a cycle")
        router.parent = self
        self.children.append(router)
        return router

    def _ancestors(self) -> Iterator[Router]:
        node: Router | None = self
        while node is not None:
            yield node
            node = node.parent

    # ---- lookup --------------------------------------------------------

    def chain(self) -> Iterator[tuple[Handler, tuple[Middleware, ...]]]:
        """Every handler reachable from here, with the middleware around it.

        Depth-first in registration order: this router's own handlers, then
        each child's. Middleware accumulates from the outside in, so a parent's
        wraps a child's.
        """
        yield from self._chain(())

    def _chain(
        self, inherited: tuple[Middleware, ...]
    ) -> Iterator[tuple[Handler, tuple[Middleware, ...]]]:
        stack = (*inherited, *self.middlewares)
        for handler in self.handlers:
            yield handler, stack
        for child in self.children:
            yield from child._chain(stack)


def _calling_module() -> str:
    """Name the router after where it was built, for readable logs.

    Walks out of this module rather than counting frames, so a subclass or a
    factory does not shift the answer by one.
    """
    frame = inspect.currentframe()
    while frame is not None:
        name = str(frame.f_globals.get("__name__", ""))
        if name and name != __name__:
            return name
        frame = frame.f_back
    return "router"  # pragma: no cover - depends on the interpreter
