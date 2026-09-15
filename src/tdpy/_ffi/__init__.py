"""Foreign-function layer: everything that touches the ``tdjson`` C ABI.

Nothing in this package imports from the rest of tdpy except
:mod:`tdpy.exceptions`, and nothing outside this package uses :mod:`ctypes`.
"""

from __future__ import annotations

from tdpy._ffi.abi import LogCallback, TDJsonABI
from tdpy._ffi.fake import FakeTDJson
from tdpy._ffi.loader import TDJsonNotFoundError, load_tdjson
from tdpy._ffi.tdjson import TDJson, get_tdjson

__all__ = [
    "FakeTDJson",
    "LogCallback",
    "TDJson",
    "TDJsonABI",
    "TDJsonNotFoundError",
    "get_tdjson",
    "load_tdjson",
]
