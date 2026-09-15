"""An in-process stand-in for ``tdjson``.

This exists because the alternative -- testing only against a compiled TDLib
and a live Telegram account -- would leave the framework effectively untested
in CI. Everything above :mod:`tdpy._ffi` is written against
:class:`~tdpy._ffi.abi.TDJsonABI`, so swapping this in exercises the real code
paths: request framing, ``@extra`` correlation, the receive thread, the
authorization state machine, dispatch.

It models TDLib's observable behaviour, not its internals: requests are
recorded, scripted responders answer them, and ``receive`` blocks like the real
thing does.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from tdpy._ffi.abi import LogCallback
from tdpy.exceptions import TdpyError

__all__ = ["FakeTDJson", "Responder", "error"]

#: Either a fixed payload, or a function of the request that returns one.
#: Returning ``None`` means "stay silent" -- the test will push a reply itself.
Responder = Mapping[str, Any] | Callable[[dict[str, Any]], Mapping[str, Any] | None]


def error(code: int, message: str) -> dict[str, Any]:
    """Build a TDLib ``error`` object, for scripting failure paths."""
    return {"@type": "error", "code": code, "message": message}


class FakeTDJson:
    """A scriptable TDLib double.

    Example:
        >>> fake = FakeTDJson()
        >>> fake.on("getMe", {"@type": "user", "id": 42})
        >>> cid = fake.create_client_id()
        >>> fake.send(cid, b'{"@type":"getMe","@extra":"1"}')
        >>> json.loads(fake.receive(0.1))["id"]
        42
    """

    def __init__(self, *, default_responder: Responder | None = None) -> None:
        self._cond = threading.Condition()
        self._outbox: deque[bytes] = deque()
        self._sent: list[dict[str, Any]] = []
        self._responders: dict[str, Responder] = {}
        self._execute_responders: dict[str, Responder] = {}
        self._default_responder = default_responder
        self._next_client_id = 0
        self._closed = False
        self.log_callback: LogCallback | None = None
        self.log_verbosity = 0

    # ---- scripting -----------------------------------------------------

    def on(self, method: str, responder: Responder) -> FakeTDJson:
        """Answer every ``method`` request with ``responder``. Chainable."""
        with self._cond:
            self._responders[method] = responder
        return self

    def on_execute(self, method: str, responder: Responder) -> FakeTDJson:
        """Answer a synchronous :meth:`execute` call. Chainable."""
        with self._cond:
            self._execute_responders[method] = responder
        return self

    def push(self, payload: Mapping[str, Any], *, client_id: int | None = None) -> None:
        """Enqueue an update or an unsolicited response for the receive loop."""
        obj = dict(payload)
        if client_id is not None:
            obj["@client_id"] = client_id
        with self._cond:
            self._outbox.append(json.dumps(obj).encode())
            self._cond.notify_all()

    # ---- introspection -------------------------------------------------

    @property
    def sent(self) -> list[dict[str, Any]]:
        """Every request sent so far, in order, as decoded dicts."""
        with self._cond:
            return list(self._sent)

    def sent_of(self, method: str) -> list[dict[str, Any]]:
        return [r for r in self.sent if r.get("@type") == method]

    def wait_for_sent(self, method: str, timeout: float = 1.0, since: int = 0) -> dict[str, Any]:
        """Block until a ``method`` request arrives at or after index ``since``.

        Lets a threaded test synchronise with a client without sleeping.
        Blocks the calling thread, so do not call it from a coroutine -- in an
        async test that is the event loop thread, and the client being waited
        on would never get to run.
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for request in self._sent[since:]:
                    if request.get("@type") == method:
                        return dict(request)
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._closed:
                    raise TdpyError(
                        f"no {method!r} request within {timeout}s; "
                        f"saw {[r.get('@type') for r in self._sent[since:]]}"
                    )
                self._cond.wait(remaining)

    # ---- TDJsonABI -----------------------------------------------------

    def create_client_id(self) -> int:
        with self._cond:
            self._next_client_id += 1
            return self._next_client_id

    def send(self, client_id: int, request: bytes) -> None:
        payload = json.loads(request)
        with self._cond:
            self._sent.append(payload)
            responder = self._responders.get(payload.get("@type"), self._default_responder)
            self._cond.notify_all()
        if responder is None:
            return
        reply = responder(payload) if callable(responder) else responder
        if reply is None:
            return
        obj = dict(reply)
        obj["@client_id"] = client_id
        # TDLib echoes @extra back verbatim; correlation upstream depends on it.
        if "@extra" in payload and "@extra" not in obj:
            obj["@extra"] = payload["@extra"]
        with self._cond:
            self._outbox.append(json.dumps(obj).encode())
            self._cond.notify_all()

    def receive(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        with self._cond:
            while not self._outbox:
                if self._closed:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            return self._outbox.popleft()

    def execute(self, request: bytes) -> bytes | None:
        payload = json.loads(request)
        method = payload.get("@type")
        with self._cond:
            responder = self._execute_responders.get(method)
        if responder is None:
            reply: Mapping[str, Any] | None = error(400, f"Method {method!r} can't be executed")
        else:
            reply = responder(payload) if callable(responder) else responder
        if reply is None:
            return None
        obj = dict(reply)
        if "@extra" in payload and "@extra" not in obj:
            obj["@extra"] = payload["@extra"]
        return json.dumps(obj).encode()

    def set_log_callback(self, max_verbosity: int, callback: LogCallback | None) -> None:
        self.log_verbosity = max_verbosity
        self.log_callback = callback

    def close(self) -> None:
        """Wake every blocked receiver and stop accepting work."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()
