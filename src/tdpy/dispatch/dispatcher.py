"""Connects a client's updates to a router's handlers.

The receive thread cannot run handlers -- it must not block, and handlers are
coroutines -- so updates are decoded, queued, and drained by worker tasks on
the event loop.

The default is a single worker, which means updates are handled **in order**.
Telegram's own ordering carries meaning (an edit after its message, a deletion
after both), and concurrency quietly discards it. Raise ``workers`` when
handlers are slow and independent, knowing what it costs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import msgspec

from tdpy.api import types
from tdpy.api.base import convert
from tdpy.core.client import Client
from tdpy.dispatch.context import Context
from tdpy.dispatch.filters import sender_id
from tdpy.dispatch.middleware import wrap
from tdpy.dispatch.router import Router, SkipHandler, subject_of
from tdpy.fsm.context import FSMContext
from tdpy.fsm.storage import StateStorage, StorageKey

__all__ = ["Dispatcher", "ErrorHandler"]

logger = logging.getLogger(__name__)

#: ``(exception, event, ctx) -> None``, called when a handler raises.
ErrorHandler = Callable[[BaseException, Any, Context], Any]


class Dispatcher:
    """Feeds updates from a client into a router.

    Args:
        router: The root router. Its children are searched too.
        storage: Conversation state backend. Without one, ``ctx.state`` is
            ``None`` and :class:`~tdpy.dispatch.filters.StateIs` only matches
            ``None``.
        workers: Concurrent handler tasks. One preserves update order.
        queue_size: Pending updates to hold. ``0`` is unbounded; a bound drops
            the oldest and counts it in :attr:`dropped`, which is better than
            growing without limit when handlers cannot keep up.
        on_error: Called when a handler raises. The default logs it. Either
            way the exception never reaches the update loop.
    """

    def __init__(
        self,
        router: Router,
        *,
        storage: StateStorage | None = None,
        workers: int = 1,
        queue_size: int = 0,
        on_error: ErrorHandler | None = None,
    ) -> None:
        if workers < 1:
            raise ValueError("workers must be at least 1")
        self.router = router
        self.storage = storage
        self.workers = workers
        self.queue_size = queue_size
        self.on_error = on_error

        self._queue: asyncio.Queue[tuple[Client, types.Update]] = asyncio.Queue(maxsize=queue_size)
        self._tasks: list[asyncio.Task[None]] = []
        self._unsubscribe: Callable[[], None] | None = None
        self._dropped = 0
        self._undecodable = 0
        self._handled = 0

    @property
    def dropped(self) -> int:
        """Updates discarded because a bounded queue overflowed."""
        return self._dropped

    @property
    def undecodable(self) -> int:
        """Updates a newer TDLib sent that this build does not know."""
        return self._undecodable

    @property
    def handled(self) -> int:
        """Updates that matched a handler and ran it."""
        return self._handled

    # ---- lifecycle -----------------------------------------------------

    async def start(self, client: Client) -> None:
        """Subscribe to ``client`` and start the workers."""
        if self._tasks:
            raise RuntimeError("dispatcher is already started")
        self._unsubscribe = client.subscribe(lambda update: self._enqueue(client, update))
        self._tasks = [
            asyncio.create_task(self._worker(), name=f"tdpy-dispatch-{index}")
            for index in range(self.workers)
        ]

    async def stop(self, *, drain: bool = True) -> None:
        """Stop accepting updates and shut the workers down.

        Args:
            drain: Finish what is already queued first. Turning it off drops
                pending updates, which for a bot means losing messages it was
                told about.
        """
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if drain and self._tasks:
            await self._queue.join()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    @contextlib.asynccontextmanager
    async def run(self, client: Client) -> AsyncIterator[Dispatcher]:
        """Dispatch for as long as the block runs.

        Example:
            >>> async with dispatcher.run(client):              # doctest: +SKIP
            ...     await stop_event.wait()
        """
        await self.start(client)
        try:
            yield self
        finally:
            await self.stop()

    # ---- intake --------------------------------------------------------

    def _enqueue(self, client: Client, payload: dict[str, Any]) -> None:
        """Runs on the event loop thread, via Client.subscribe."""
        try:
            update: types.Update = convert(payload, types.Update)
        except msgspec.ValidationError:
            # A newer TDLib knows update types this build does not. Dropping
            # one is survivable; refusing to decode anything else is not.
            self._undecodable += 1
            logger.debug("ignoring unknown update %s", payload.get("@type"))
            return

        try:
            self._queue.put_nowait((client, update))
        except asyncio.QueueFull:
            self._dropped += 1
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
                self._queue.task_done()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait((client, update))

    async def feed(self, client: Client, update: types.Update) -> None:
        """Dispatch one update directly, bypassing the queue.

        Exists for tests and for replaying updates. It waits for the handler to
        finish, but a handler's exception is *not* raised here: it goes to
        ``on_error``, or to the log, exactly as in live dispatch. A test that
        feeds updates should collect errors through ``on_error`` and assert
        there were none, or a crashing handler will pass.
        """
        await self._dispatch(client, update)

    # ---- dispatch ------------------------------------------------------

    async def _worker(self) -> None:
        while True:
            client, update = await self._queue.get()
            try:
                await self._dispatch(client, update)
            except Exception as error:
                self._report(error, update, None)
            finally:
                self._queue.task_done()

    async def _dispatch(self, client: Client, update: types.Update) -> None:
        subject = subject_of(update)
        ctx = Context(client=client, update=update)
        if self.storage is not None:
            ctx.state = FSMContext(self.storage, self._key(client, subject, update))

        for handler, middlewares in self.router.chain():
            # A type-specific handler gets the subject; an on_update() handler
            # gets the update, because that is what it asked for.
            event = subject if handler.wants_subject else update
            if not await handler.matches(event, ctx):
                continue
            try:
                await wrap(handler, middlewares)(event, ctx)
            except SkipHandler:
                # The handler looked and declined; keep searching.
                continue
            except Exception as error:
                self._report(error, event, ctx)
            self._handled += 1
            return

    def _key(self, client: Client, event: Any, update: types.Update) -> StorageKey:
        """Identify the conversation an update belongs to.

        Falls back to the chat when no user can be identified -- a channel post
        has no sender -- so state stays scoped rather than shared globally.
        """
        chat_id = getattr(event, "chat_id", None) or getattr(update, "chat_id", None) or 0
        user_id = sender_id(event)
        if user_id is None:
            user_id = getattr(event, "user_id", None) or chat_id
        return StorageKey(chat_id=int(chat_id), user_id=int(user_id), client_id=client.client_id)

    def _report(self, error: BaseException, event: Any, ctx: Context | None) -> None:
        """One failing handler must not take the dispatcher down with it."""
        if self.on_error is not None and ctx is not None:
            try:
                self.on_error(error, event, ctx)
                return
            except Exception:
                logger.exception("the error handler itself raised")
        logger.error("handler failed", exc_info=error)
