"""Errors returned by TDLib, and the framework errors around them.

TDLib reports failures as an ordinary ``error`` object rather than a transport
failure, so every one of them arrives through the same path as a success. This
module turns those payloads into exceptions specific enough to branch on --
above all :class:`FloodWait`, which carries the one number a caller needs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from tdpy.exceptions import TdpyError

__all__ = [
    "BadRequest",
    "ClientClosedError",
    "ClientStateError",
    "FloodWait",
    "Forbidden",
    "InternalError",
    "NotFound",
    "ReceiverError",
    "TDLibError",
    "Unauthorized",
    "error_from_payload",
]

# TDLib passes MTProto's FLOOD_WAIT_x through verbatim in some paths and
# rewrites it as an HTTP-style message in others. Both mean the same thing.
_FLOOD_PATTERNS = (
    re.compile(r"FLOOD_WAIT_(\d+)"),
    re.compile(r"retry after (\d+)", re.IGNORECASE),
)


class TDLibError(TdpyError):
    """An ``error`` object returned by TDLib.

    Attributes:
        code: TDLib's numeric code, mirroring HTTP status conventions.
        message: TDLib's message, usually a SCREAMING_SNAKE constant.
        request: The request that provoked it, when the caller knows it.
    """

    def __init__(
        self,
        code: int,
        message: str,
        *,
        request: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.request = dict(request) if request is not None else None
        super().__init__(self._describe())

    def _describe(self) -> str:
        method = (self.request or {}).get("@type")
        suffix = f" (in {method})" if method else ""
        return f"[{self.code}] {self.message}{suffix}"


class BadRequest(TDLibError):
    """400 -- the request was malformed or the arguments were rejected."""


class Unauthorized(TDLibError):
    """401 -- the client is not authorized; re-run the authorization flow."""


class Forbidden(TDLibError):
    """403 -- authorized, but not permitted to do this."""


class NotFound(TDLibError):
    """404 -- the chat, message, or file does not exist."""


class FloodWait(TDLibError):
    """429/420 -- rate limited. Retry no earlier than ``retry_after`` seconds.

    Ignoring this and retrying immediately escalates the limit, so the value is
    surfaced as a number rather than left inside the message text.
    """

    def __init__(
        self,
        code: int,
        message: str,
        *,
        retry_after: float,
        request: Mapping[str, Any] | None = None,
    ) -> None:
        self.retry_after = retry_after
        super().__init__(code, message, request=request)

    def _describe(self) -> str:
        return f"{super()._describe()} -- retry after {self.retry_after:g}s"


class InternalError(TDLibError):
    """5xx -- a TDLib or Telegram server fault. Usually worth retrying."""


class ClientStateError(TdpyError):
    """The client was used in a state that does not allow it."""


class ClientClosedError(ClientStateError):
    """The client was closed while a request was still in flight."""


class ReceiverError(TdpyError):
    """The receive loop died, so no further response can ever arrive."""


_BY_CODE: dict[int, type[TDLibError]] = {
    400: BadRequest,
    401: Unauthorized,
    403: Forbidden,
    404: NotFound,
    406: BadRequest,
}


def _parse_retry_after(message: str) -> float | None:
    for pattern in _FLOOD_PATTERNS:
        match = pattern.search(message)
        if match:
            return float(match.group(1))
    return None


def error_from_payload(
    payload: Mapping[str, Any],
    request: Mapping[str, Any] | None = None,
) -> TDLibError:
    """Build the most specific exception for a TDLib ``error`` payload."""
    code = int(payload.get("code", 0))
    message = str(payload.get("message", ""))

    retry_after = _parse_retry_after(message)
    if retry_after is not None or code in (420, 429):
        # A rate limit without a parsable delay is still a rate limit; 0 lets
        # callers apply their own backoff rather than guess at a wrong number.
        return FloodWait(code, message, retry_after=retry_after or 0.0, request=request)

    if code in _BY_CODE:
        return _BY_CODE[code](code, message, request=request)
    if code >= 500:
        return InternalError(code, message, request=request)
    return TDLibError(code, message, request=request)
