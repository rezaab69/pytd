"""The single thread allowed to call ``td_receive``.

TDLib's receive queue is per-process, not per-client: one ``td_receive`` loop
returns responses and updates for every client id, tagged with ``@client_id``.
So this module owns exactly one thread per backend, demultiplexes what it
reads, and hands each payload to the right subscriber.

The thread is reference-counted by subscriber. It starts when the first client
registers and stops when the last one leaves, which means an application that
opens and closes clients does not accumulate threads.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from typing import Any, Protocol

from tdpy._ffi.abi import TDJsonABI
from tdpy.core.errors import ReceiverError
from tdpy.core.serde import decode

__all__ = ["Receiver", "Subscriber", "get_receiver"]

logger = logging.getLogger(__name__)

#: How long a single ``td_receive`` call blocks. Bounds shutdown latency: the
#: thread cannot notice a stop request while it is inside the C call.
DEFAULT_POLL_TIMEOUT = 1.0


class Subscriber(Protocol):
    """What a :class:`Receiver` needs from a client.

    Both methods run **on the receive thread**, so neither may block: an
    implementation should hand the payload to its event loop and return.
    """

    def on_payload(self, payload: dict[str, Any]) -> None:
        """Deliver one response or update addressed to this subscriber."""

    def on_receiver_failure(self, error: BaseException) -> None:
        """The loop died. Nothing more will arrive; fail anything pending."""


class Receiver:
    """Owns the receive thread for one backend."""

    def __init__(self, backend: TDJsonABI, *, poll_timeout: float = DEFAULT_POLL_TIMEOUT) -> None:
        self.backend = backend
        self.poll_timeout = poll_timeout
        self._subscribers: dict[int, Subscriber] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failure: BaseException | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def failure(self) -> BaseException | None:
        """The exception that killed the loop, if it died."""
        return self._failure

    def register(self, client_id: int, subscriber: Subscriber) -> None:
        """Route payloads for ``client_id``, starting the thread if needed."""
        with self._lock:
            if self._failure is not None:
                raise ReceiverError("receive loop already failed") from self._failure
            if client_id in self._subscribers:
                raise ReceiverError(f"client id {client_id} is already registered")
            self._subscribers[client_id] = subscriber
            self._ensure_started()

    def unregister(self, client_id: int) -> None:
        """Stop routing for ``client_id``; stop the thread if it was the last."""
        with self._lock:
            self._subscribers.pop(client_id, None)
            if self._subscribers:
                return
        self.stop()

    def _ensure_started(self) -> None:
        """Caller must hold ``self._lock``."""
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"tdpy-receiver-{id(self.backend):x}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        """Ask the thread to finish and wait for it. Idempotent."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            # The thread can be parked inside td_receive for up to poll_timeout.
            thread.join(timeout if timeout is not None else self.poll_timeout * 2)
        self._thread = None
        _forget(self.backend, self)

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                raw = self.backend.receive(self.poll_timeout)
                if raw is None:
                    continue
                self._dispatch(raw)
        except BaseException as exc:
            self._fail(exc)

    def _dispatch(self, raw: bytes) -> None:
        try:
            payload = decode(raw)
        except Exception:
            # One unparsable payload must not take down the loop for everyone.
            logger.exception("discarding undecodable payload from TDLib")
            return

        subscriber = self._route(payload)
        if subscriber is None:
            logger.debug("no subscriber for payload %s", payload.get("@type"))
            return
        try:
            subscriber.on_payload(payload)
        except Exception:
            logger.exception("subscriber raised while handling %s", payload.get("@type"))

    def _route(self, payload: Mapping[str, Any]) -> Subscriber | None:
        client_id = payload.get("@client_id")
        with self._lock:
            if client_id is not None:
                return self._subscribers.get(int(client_id))
            # Untagged payloads are not expected from the modern interface, but
            # with a single client there is exactly one place they can belong.
            if len(self._subscribers) == 1:
                return next(iter(self._subscribers.values()))
            logger.warning(
                "untagged payload %s with %d subscribers",
                payload.get("@type"),
                len(self._subscribers),
            )
            return None

    def _fail(self, exc: BaseException) -> None:
        """Tell every subscriber the loop is gone, so nothing waits forever."""
        logger.exception("receive loop died", exc_info=exc)
        with self._lock:
            self._failure = exc
            subscribers = list(self._subscribers.values())
            self._subscribers.clear()
        for subscriber in subscribers:
            try:
                subscriber.on_receiver_failure(exc)
            except Exception:
                logger.exception("subscriber raised while handling receiver failure")
        _forget(self.backend, self)


# One receiver per backend, shared by every client using it. Keyed by the
# backend object itself and removed deterministically when the last subscriber
# leaves, so no weak references and no stale entries.
_registry: dict[int, Receiver] = {}
_registry_lock = threading.Lock()


def get_receiver(backend: TDJsonABI, *, poll_timeout: float = DEFAULT_POLL_TIMEOUT) -> Receiver:
    """Return the shared :class:`Receiver` for ``backend``, creating it once."""
    with _registry_lock:
        existing = _registry.get(id(backend))
        if existing is not None:
            return existing
        receiver = Receiver(backend, poll_timeout=poll_timeout)
        _registry[id(backend)] = receiver
        return receiver


def _forget(backend: TDJsonABI, receiver: Receiver) -> None:
    with _registry_lock:
        if _registry.get(id(backend)) is receiver:
            del _registry[id(backend)]
