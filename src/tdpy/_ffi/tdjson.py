"""``ctypes`` binding for TDLib's JSON interface.

Only the five non-deprecated entry points are bound. The older
``td_json_client_*`` family is scheduled for removal in TDLib 2.0.0 and is
deliberately not exposed.

Two invariants live in this module and nowhere else:

1. **The returned pointer of ``td_receive``/``td_execute`` is owned by TDLib
   and is freed on the next call in the same thread.** Declaring ``restype``
   as :class:`ctypes.c_char_p` makes ctypes copy the NUL-terminated string
   into a fresh Python ``bytes`` before returning, so nothing borrowed ever
   escapes this file. Do not "optimise" that into ``c_void_p`` + a cached
   buffer.
2. **``td_receive`` must never run on two threads at once.** The C API does
   not check; it just corrupts. A non-blocking lock turns the corruption into
   an exception.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import threading
from pathlib import Path

from tdpy._ffi.abi import LogCallback
from tdpy._ffi.loader import load_tdjson
from tdpy.exceptions import TdpyError

__all__ = ["ConcurrentReceiveError", "TDJson", "get_tdjson", "reset_tdjson"]

logger = logging.getLogger("tdpy.tdlib")

_LOG_CALLBACK_TYPE = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p)

# TDLib keeps the raw function pointer we hand it, so the ctypes trampoline must
# outlive any Python reference to the TDJson that installed it.
_live_callbacks: list[object] = []

#: TDLib writes its internal log to stderr at verbosity 5 unless told not
#: to -- around 70 lines for a second of idling. A library has no business
#: doing that, so tdpy quiets it and routes what remains through `logging`,
#: where the application decides what to show.
DEFAULT_LOG_VERBOSITY = 1

# TDLib verbosity: 0 fatal, 1 error, 2 warning, 3 info, 4+ debug.
_LOG_LEVELS = {0: logging.CRITICAL, 1: logging.ERROR, 2: logging.WARNING, 3: logging.INFO}

_singleton_lock = threading.Lock()
_singleton: TDJson | None = None


class ConcurrentReceiveError(TdpyError):
    """Raised when ``receive`` is entered while another thread is inside it."""


class TDJson:
    """A loaded ``tdjson`` library.

    TDLib's receive queue is process-global rather than per-instance, so in
    normal use there is exactly one of these per process -- see
    :func:`get_tdjson`. Constructing extra instances is supported for tests but
    they all share the same underlying C state.
    """

    def __init__(
        self,
        library_path: str | Path | None = None,
        *,
        log_verbosity: int | None = DEFAULT_LOG_VERBOSITY,
    ) -> None:
        """Load the library.

        Args:
            library_path: An exact library file or a directory to search.
            log_verbosity: Quiet TDLib to this level and forward what is left
                to the ``tdpy.tdlib`` logger. ``None`` leaves TDLib's logging
                exactly as it was, stderr flood included.
        """
        self._lib, self.library_path = load_tdjson(library_path)
        self._receive_lock = threading.Lock()
        self._receive_owner: int | None = None
        self._closed = False
        self._bind()
        if log_verbosity is not None:
            self.set_log_verbosity(log_verbosity)
            self.forward_logs_to_logging(log_verbosity)

    def _bind(self) -> None:
        lib = self._lib

        lib.td_create_client_id.restype = ctypes.c_int
        lib.td_create_client_id.argtypes = []

        lib.td_send.restype = None
        lib.td_send.argtypes = [ctypes.c_int, ctypes.c_char_p]

        # c_char_p restype => ctypes copies the string out for us. See module docstring.
        lib.td_receive.restype = ctypes.c_char_p
        lib.td_receive.argtypes = [ctypes.c_double]

        lib.td_execute.restype = ctypes.c_char_p
        lib.td_execute.argtypes = [ctypes.c_char_p]

        lib.td_set_log_message_callback.restype = None
        lib.td_set_log_message_callback.argtypes = [ctypes.c_int, _LOG_CALLBACK_TYPE]

    def create_client_id(self) -> int:
        return int(self._lib.td_create_client_id())

    def send(self, client_id: int, request: bytes) -> None:
        self._lib.td_send(client_id, request)

    def receive(self, timeout: float) -> bytes | None:
        if not self._receive_lock.acquire(blocking=False):
            raise ConcurrentReceiveError(
                f"td_receive is already running on thread {self._receive_owner}; "
                "TDLib allows only one receiving thread per process"
            )
        self._receive_owner = threading.get_ident()
        try:
            result: bytes | None = self._lib.td_receive(ctypes.c_double(timeout))
            return result
        finally:
            self._receive_owner = None
            self._receive_lock.release()

    def execute(self, request: bytes) -> bytes | None:
        result: bytes | None = self._lib.td_execute(request)
        return result

    def set_log_callback(self, max_verbosity: int, callback: LogCallback | None) -> None:
        """Install ``callback`` as TDLib's log sink, or restore the default.

        TDLib may invoke it from any of its internal threads, so the trampoline
        swallows exceptions: raising across the FFI boundary would abort the
        process, and a logging failure is never worth that.
        """
        if callback is None:
            self._lib.td_set_log_message_callback(max_verbosity, _LOG_CALLBACK_TYPE())
            return

        def trampoline(verbosity: int, message: bytes) -> None:
            # An exception here would unwind into C and abort the process.
            with contextlib.suppress(Exception):
                callback(verbosity, message.decode("utf-8", "replace").rstrip("\n"))

        holder = _LOG_CALLBACK_TYPE(trampoline)
        _live_callbacks.append(holder)
        self._lib.td_set_log_message_callback(max_verbosity, holder)

    def set_log_verbosity(self, level: int) -> None:
        """Set how much TDLib logs at all.

        0 is fatal only, 1 errors, 2 warnings, 3 info, 4 and up debug. This is
        process-wide and takes effect immediately; it is what actually stops
        the stderr output, since installing a log callback does not.
        """
        self.execute(
            json.dumps({"@type": "setLogVerbosityLevel", "new_verbosity_level": level}).encode()
        )

    def forward_logs_to_logging(self, max_verbosity: int = 2) -> None:
        """Convenience wiring of TDLib's log into the ``tdpy.tdlib`` logger."""

        def emit(verbosity: int, message: str) -> None:
            logger.log(_LOG_LEVELS.get(verbosity, logging.DEBUG), "%s", message)

        self.set_log_callback(max_verbosity, emit)

    def close(self) -> None:
        """Detach our log callback. Individual clients are closed via TDLib."""
        if self._closed:
            return
        self._closed = True
        self.set_log_callback(0, None)


def get_tdjson(library_path: str | Path | None = None) -> TDJson:
    """Return the process-wide :class:`TDJson`, loading it on first use.

    ``library_path`` only has an effect on the first call; afterwards the
    already-loaded library is returned regardless.
    """
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = TDJson(library_path)
        return _singleton


def reset_tdjson() -> None:
    """Drop the cached instance. Intended for tests; does not unload the library."""
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            _singleton.close()
        _singleton = None
