"""Hand-written support for the generated API.

``types.py``, ``functions.py`` and ``methods.py`` in this package are produced
by :mod:`tdpy.codegen` and must not be edited. This module is the opposite: it
is the stable hand-written floor they are generated against.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, overload

import msgspec

__all__ = [
    "Int64",
    "TDObject",
    "convert",
    "dec_hook",
    "decode",
    "enc_hook",
    "encode",
    "to_builtins",
]

T = TypeVar("T")


class Int64(int):
    """A 64-bit integer, which TDLib sends as a JSON *string*.

    JSON numbers are doubles, so anything past 2^53 loses precision. Chat, user
    and message identifiers are all int64, which means decoding them as plain
    JSON numbers silently corrupts real-world ids. This type carries the string
    encoding through :func:`encode` and :func:`decode` while behaving as an
    ordinary ``int`` everywhere else.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return f"Int64({int(self)})"


def dec_hook(tp: type, obj: Any) -> Any:
    """Decode the types msgspec does not know natively."""
    if tp is Int64:
        return Int64(obj)
    raise NotImplementedError(f"cannot decode {tp!r}")


def enc_hook(obj: Any) -> Any:
    """Encode the types msgspec does not know natively."""
    if isinstance(obj, Int64):
        return str(int(obj))
    raise NotImplementedError(f"cannot encode {type(obj)!r}")


class TDObject(msgspec.Struct, tag_field="@type", kw_only=True, omit_defaults=True):
    """Base of every generated TDLib type and function.

    The three struct options are all load-bearing:

    * ``tag_field="@type"`` is how msgspec picks the right member of a union --
      TDLib's 211 abstract classes are decoded entirely by this tag.
    * ``omit_defaults`` keeps unset optional arguments off the wire, so
      ``SendMessage(chat_id=1, ...)`` does not send a null ``reply_markup``.
    * ``kw_only`` because TL declaration order is not a useful call order, and
      it frees fields with defaults from having to come last.
    """

    @property
    def type_name(self) -> str:
        """The TDLib ``@type`` tag for this object, for example ``sendMessage``."""
        tag = self.__struct_config__.tag
        return tag if isinstance(tag, str) else type(self).__name__


_encoder = msgspec.json.Encoder(enc_hook=enc_hook)


def encode(obj: Any) -> bytes:
    """Serialize a request object to JSON bytes."""
    return _encoder.encode(obj)


# One decoder per result type: building them is far costlier than using them.
# A plain dict rather than functools.cache, which cannot express "any hashable
# type object" without tripping the type checker.
_decoders: dict[Any, msgspec.json.Decoder[Any]] = {}


def _decoder(tp: Any) -> msgspec.json.Decoder[Any]:
    cached = _decoders.get(tp)
    if cached is None:
        cached = _decoders[tp] = msgspec.json.Decoder(tp, dec_hook=dec_hook)
    return cached


@overload
def decode(raw: bytes, tp: type[T]) -> T: ...


@overload
def decode(raw: bytes, tp: Any) -> Any: ...


def decode(raw: bytes, tp: Any) -> Any:
    """Deserialize JSON bytes into ``tp``.

    ``tp`` may be a generated struct, which types the result exactly, or one of
    the 211 union aliases -- a union is not a ``type`` and cannot bind a type
    variable, so those fall to the second overload and are typed at the call
    site instead.
    """
    return _decoder(tp).decode(raw)


@overload
def convert(payload: Mapping[str, Any], tp: type[T]) -> T: ...


@overload
def convert(payload: Mapping[str, Any], tp: Any) -> Any: ...


def convert(payload: Mapping[str, Any], tp: Any) -> Any:
    """Build a typed object from an already-decoded payload.

    The receive loop has to parse every payload as a dict anyway, to read
    ``@client_id`` and ``@extra``. Converting from that dict avoids a pointless
    re-encode round trip, and unknown fields -- such as a newer TDLib's
    additions -- are ignored rather than raising.

    As with :func:`decode`, ``tp`` may be a struct or a union alias.
    """
    return msgspec.convert(payload, tp, dec_hook=dec_hook)


def to_builtins(obj: Any) -> dict[str, Any]:
    """Turn a request object into the plain dict the core client sends."""
    result: dict[str, Any] = msgspec.to_builtins(obj, enc_hook=enc_hook)
    return result
