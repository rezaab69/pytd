"""The asyncio client: requests in, responses and updates out.

Two threads meet here. The receive thread calls :meth:`Client.on_payload` and
must not block; everything it hands over is bounced onto the event loop with
``call_soon_threadsafe``. As a result ``_pending`` is only ever touched from
the loop thread and needs no lock -- do not "simplify" that by reaching into it
from the receive thread.

Correlation uses TDLib's ``@extra`` field, which it echoes back on the response
verbatim. Updates never carry one, which is exactly what separates the two
streams.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, TypeVar

from tdpy._ffi.abi import TDJsonABI
from tdpy._ffi.tdjson import get_tdjson
from tdpy.core.errors import (
    ClientClosedError,
    ClientStateError,
    ReceiverError,
    TDLibError,
    error_from_payload,
)
from tdpy.core.receiver import DEFAULT_POLL_TIMEOUT, Receiver, get_receiver
from tdpy.core.retry import RetryPolicy
from tdpy.core.serde import decode, encode

__all__ = ["DEFAULT_CLOSE_TIMEOUT", "Client", "UpdateObserver"]

#: Called on the event loop thread for each update, before it is queued.
UpdateObserver = Callable[[dict[str, Any]], None]

#: Lets start() and __aenter__ hand back the subclass they were called on,
#: so `async with TypedClient()` keeps its 1022 typed methods instead of
#: narrowing to Client. (typing.Self would do it, but only from 3.11.)
SelfT = TypeVar("SelfT", bound="Client")

logger = logging.getLogger(__name__)

#: How long :meth:`Client.close` waits for TDLib to flush its database.
DEFAULT_CLOSE_TIMEOUT = 10.0


class _StreamClosed:
    """Sentinel pushed onto the update queue when the stream ends."""


class Client:
    """One TDLib client instance, driven from asyncio.

    Example:
        >>> async with Client() as client:                      # doctest: +SKIP
        ...     me = await client.invoke({"@type": "getMe"})
        ...     print(me["first_name"])

    Args:
        backend: The tdjson backend. Defaults to the process-wide real library;
            pass a :class:`~tdpy._ffi.fake.FakeTDJson` in tests.
        library_path: Where to find libtdjson, when ``backend`` is not given.
        retry: Default policy for :meth:`invoke`. ``None`` means no retries,
            because most TDLib methods are not idempotent.
        poll_timeout: Receive-loop block time; also bounds shutdown latency.
        update_queue_size: ``0`` for unbounded. A bound protects memory when
            updates arrive faster than they are consumed, at the cost of
            dropping the oldest ones -- see :attr:`dropped_updates`.
    """

    def __init__(
        self,
        *,
        backend: TDJsonABI | None = None,
        library_path: str | Path | None = None,
        retry: RetryPolicy | None = None,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT,
        update_queue_size: int = 0,
    ) -> None:
        self._backend = backend
        self._library_path = library_path
        self._poll_timeout = poll_timeout
        self._update_queue_size = update_queue_size
        self.retry = retry

        self._client_id: int | None = None
        self._receiver: Receiver | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._queue: asyncio.Queue[dict[str, Any] | _StreamClosed] | None = None
        self._extras = itertools.count(1)
        self._closed_event: asyncio.Event | None = None
        self._failure: BaseException | None = None
        self._dropped_updates = 0
        self._running = False
        self._observers: list[UpdateObserver] = []

    # ---- lifecycle -----------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def client_id(self) -> int:
        """TDLib's identifier for this client. Only valid once started."""
        if self._client_id is None:
            raise ClientStateError("client has not been started")
        return self._client_id

    @property
    def backend(self) -> TDJsonABI:
        """The tdjson backend, loading the real library on first access."""
        if self._backend is None:
            self._backend = get_tdjson(self._library_path)
        return self._backend

    @property
    def dropped_updates(self) -> int:
        """Updates discarded because a bounded queue overflowed."""
        return self._dropped_updates

    async def start(self: SelfT) -> SelfT:
        """Allocate a TDLib client and attach it to the receive loop."""
        if self._running:
            raise ClientStateError("client is already started")
        if self._client_id is not None:
            raise ClientStateError("a closed client cannot be restarted")

        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self._update_queue_size)
        self._closed_event = asyncio.Event()
        self._client_id = self.backend.create_client_id()
        self._receiver = get_receiver(self.backend, poll_timeout=self._poll_timeout)
        self._receiver.register(self._client_id, self)
        self._running = True
        return self

    async def close(self, *, timeout: float = DEFAULT_CLOSE_TIMEOUT) -> None:
        """Close the TDLib client and stop receiving. Idempotent.

        Sends ``close`` and waits for ``authorizationStateClosed`` so TDLib can
        flush its database; skipping that wait risks losing local state.
        """
        if not self._running:
            return
        self._running = False

        try:
            self.backend.send(self.client_id, encode({"@type": "close"}))
            if self._closed_event is not None:
                await asyncio.wait_for(self._closed_event.wait(), timeout)
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("TDLib did not confirm close within %ss", timeout)
        except Exception:
            logger.exception("error while asking TDLib to close")
        finally:
            receiver, self._receiver = self._receiver, None
            self._fail_pending(ClientClosedError("client was closed"))
            self._close_stream()
            if receiver is not None:
                # Unregistering may join the receive thread, which can be
                # parked inside td_receive for up to poll_timeout. Doing that
                # inline would stall the event loop for every other task.
                await asyncio.to_thread(receiver.unregister, self.client_id)

    def _fail_pending(self, reason: BaseException) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(reason)
        self._pending.clear()

    def _close_stream(self) -> None:
        if self._queue is None:
            return
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(_StreamClosed())

    async def __aenter__(self: SelfT) -> SelfT:
        return await self.start()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # ---- requests ------------------------------------------------------

    async def invoke(
        self,
        request: Mapping[str, Any],
        *,
        retry: RetryPolicy | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send ``request`` and await its response.

        Args:
            request: A TDLib function object; its ``@extra`` is set by us.
            retry: Overrides the client default for this call only.
            timeout: Give up after this many seconds. ``None`` waits forever,
                which is usually right -- TDLib queues requests while offline.

        Raises:
            TDLibError: if TDLib answered with an ``error`` object.
            ClientClosedError: if the client closed while waiting.
            ReceiverError: if the receive loop died while waiting.
        """
        policy = retry if retry is not None else self.retry
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self._invoke_once(request, timeout)
            except TDLibError as error:
                delay = policy.delay_for(error, attempt) if policy is not None else None
                if delay is None:
                    raise
                logger.debug("retrying %s in %.2fs after %s", request.get("@type"), delay, error)
                await asyncio.sleep(delay)

    async def _invoke_once(
        self, request: Mapping[str, Any], timeout: float | None
    ) -> dict[str, Any]:
        loop = self._require_running()
        extra = f"tdpy-{next(self._extras)}"
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[extra] = future

        try:
            self.backend.send(self.client_id, encode({**request, "@extra": extra}))
            payload = await (asyncio.wait_for(future, timeout) if timeout else future)
        finally:
            self._pending.pop(extra, None)

        if payload.get("@type") == "error":
            raise error_from_payload(payload, request)
        return payload

    def send_nowait(self, request: Mapping[str, Any]) -> None:
        """Send without awaiting. Any response is discarded.

        For fire-and-forget calls such as ``setLogVerbosityLevel``.
        """
        self._require_running()
        self.backend.send(self.client_id, encode(request))

    def execute(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Run one of TDLib's 28 synchronous methods in the calling thread.

        No client id and no event loop are involved, so this works before
        :meth:`start`.

        Raises:
            TDLibError: if TDLib answered with an ``error`` object, or if the
                method is not one that can be executed synchronously.
        """
        raw = self.backend.execute(encode(request))
        if raw is None:
            method = request.get("@type")
            raise TDLibError(0, f"{method} returned nothing", request=request)
        payload = decode(raw)
        if payload.get("@type") == "error":
            raise error_from_payload(payload, request)
        return payload

    def _require_running(self) -> asyncio.AbstractEventLoop:
        """Assert the client can take work, and hand back its event loop."""
        if self._failure is not None:
            raise ReceiverError("receive loop is dead") from self._failure
        if not self._running or self._loop is None:
            raise ClientStateError("client is not running; call start() first")
        return self._loop

    # ---- updates -------------------------------------------------------

    def subscribe(self, observer: UpdateObserver) -> Callable[[], None]:
        """Watch every update as it arrives, without consuming :meth:`updates`.

        Returns a callable that unsubscribes. Observers run on the event loop
        thread, before the update is queued, so one must not block -- and its
        exceptions are logged rather than propagated, since an observer has no
        business killing the update stream for everyone else.

        This is how the authorization flow follows ``updateAuthorizationState``
        while leaving the application's own stream intact.
        """
        self._observers.append(observer)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._observers.remove(observer)

        return unsubscribe

    async def updates(self) -> AsyncIterator[dict[str, Any]]:
        """Yield updates until the client closes.

        Raises:
            ReceiverError: if the receive loop died rather than closing cleanly.
        """
        if self._queue is None:
            raise ClientStateError("client has not been started")
        queue = self._queue
        while True:
            item = await queue.get()
            if isinstance(item, _StreamClosed):
                if self._failure is not None:
                    raise ReceiverError("receive loop died") from self._failure
                return
            yield item

    # ---- Subscriber: these two run on the receive thread ---------------

    def on_payload(self, payload: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._handle, payload)
        except RuntimeError:
            # The loop closed underneath us; there is nowhere to deliver this.
            logger.debug("dropping %s: event loop is gone", payload.get("@type"))

    def on_receiver_failure(self, error: BaseException) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._handle_failure, error)
        except RuntimeError:
            logger.debug("receive loop failed after the event loop closed")

    # ---- everything below runs on the event loop thread ----------------

    def _handle(self, payload: dict[str, Any]) -> None:
        extra = payload.get("@extra")
        if extra is not None:
            future = self._pending.pop(str(extra), None)
            if future is not None and not future.done():
                future.set_result(payload)
            else:
                # A response to a call that already timed out or was cancelled.
                # It is not an update, so it must not reach the update stream.
                logger.debug("discarding late response for @extra=%s", extra)
            return

        if self._is_closed_notification(payload) and self._closed_event is not None:
            self._closed_event.set()

        for observer in list(self._observers):
            try:
                observer(payload)
            except Exception:
                logger.exception("update observer raised on %s", payload.get("@type"))

        self._offer_update(payload)

    @staticmethod
    def _is_closed_notification(payload: Mapping[str, Any]) -> bool:
        if payload.get("@type") != "updateAuthorizationState":
            return False
        state = payload.get("authorization_state")
        return isinstance(state, Mapping) and state.get("@type") == "authorizationStateClosed"

    def _offer_update(self, payload: dict[str, Any]) -> None:
        if self._queue is None:
            return
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Bounded queue: keep the newest, since the stalest updates are the
            # ones least likely to still matter. Count them so loss is visible.
            self._dropped_updates += 1
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(payload)

    def _handle_failure(self, error: BaseException) -> None:
        self._failure = error
        self._running = False
        self._receiver = None
        self._fail_pending(ReceiverError("receive loop died"))
        self._close_stream()
