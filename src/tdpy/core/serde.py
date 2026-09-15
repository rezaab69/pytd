"""JSON encoding for the TDLib wire format.

Isolated into one module so the serializer choice is a single edit. msgspec is
used because TDLib's tagged unions (``@type`` as the discriminator) map onto
its ``Struct`` tagging exactly, which is what Phase 3's generated API will need
-- and because the receive loop decodes every update the account produces.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import msgspec

__all__ = ["decode", "encode"]

_encoder = msgspec.json.Encoder()
_decoder = msgspec.json.Decoder(dict)


def encode(payload: Mapping[str, Any]) -> bytes:
    """Serialize a request. TDLib requires a JSON object at the top level."""
    return _encoder.encode(payload)


def decode(raw: bytes) -> dict[str, Any]:
    """Deserialize one response or update."""
    return _decoder.decode(raw)
