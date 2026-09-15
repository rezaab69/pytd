"""The contract every tdjson backend must satisfy.

Two implementations exist: :class:`tdpy._ffi.tdjson.TDJson` (real ``ctypes``
binding) and :class:`tdpy._ffi.fake.FakeTDJson` (in-process test double).
Every layer above this one is written against the protocol, never against the
concrete class, which is what lets the whole framework be tested without a
compiled TDLib on the machine.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

__all__ = ["LogCallback", "TDJsonABI"]

#: Called with ``(verbosity_level, message)``. Verbosity 0 means TDLib is about
#: to abort the process, so the callback must return promptly and must never
#: call back into TDLib.
LogCallback = Callable[[int, str], None]


@runtime_checkable
class TDJsonABI(Protocol):
    """A thin, byte-oriented view of TDLib's JSON interface.

    Methods take and return raw ``bytes`` rather than decoded objects: choosing
    a serializer is the next layer's job, and keeping it out of here means the
    FFI layer has no third-party dependencies at all.
    """

    def create_client_id(self) -> int:
        """Allocate a new TDLib client instance and return its identifier.

        The client is not started until the first :meth:`send` for that id.
        """

    def send(self, client_id: int, request: bytes) -> None:
        """Queue a request. Never blocks, never reports delivery failures."""

    def receive(self, timeout: float) -> bytes | None:
        """Wait up to ``timeout`` seconds for one response or update.

        Returns ``None`` on timeout. **Must not be called concurrently from two
        threads** -- this is a hard requirement of the underlying C API, not a
        style preference; violating it corrupts memory.
        """

    def execute(self, request: bytes) -> bytes | None:
        """Run one of the 28 synchronously-callable methods in-thread."""

    def set_log_callback(self, max_verbosity: int, callback: LogCallback | None) -> None:
        """Route TDLib's internal log up to ``max_verbosity`` into ``callback``."""

    def close(self) -> None:
        """Release backend resources. Idempotent."""
