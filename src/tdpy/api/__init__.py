"""The typed TDLib API.

Four modules live here. ``base`` is hand-written; the rest are produced by
``python -m tdpy.codegen`` from ``td_api.tl`` and must not be edited:

``types``
    Every TDLib data type, plus a union alias per abstract class.
``functions``
    Every TDLib function as a request object.
``methods``
    Typed coroutines wrapping those requests.
``client``
    :class:`~tdpy.api.client.TypedClient`, the two joined together.

Importing ``types`` costs about a fifth of a second because it builds 2183
struct classes, so nothing in :mod:`tdpy` or :mod:`tdpy.core` imports it. Reach
for the submodule you need and pay for it only then.
"""

from __future__ import annotations

from tdpy.api.base import Int64, TDObject, convert, decode, encode, to_builtins

__all__ = ["Int64", "TDObject", "convert", "decode", "encode", "to_builtins"]
