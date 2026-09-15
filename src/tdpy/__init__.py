"""tdpy -- a complete async Python framework for Telegram's TDLib."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tdpy.exceptions import TdpyError

if TYPE_CHECKING:
    from tdpy.api.client import TypedClient
    from tdpy.core.client import Client

__version__ = "0.1.0.dev0"
__all__ = ["Client", "TdpyError", "TypedClient", "__version__"]

# Both clients are resolved on first use. Importing the typed one costs about a
# fifth of a second, because it builds 2183 struct classes, and `import tdpy`
# should not charge that to people who never touch it. The TYPE_CHECKING block
# above is what keeps both names fully typed despite the indirection.
_LAZY = {
    "Client": ("tdpy.core.client", "Client"),
    "TypedClient": ("tdpy.api.client", "TypedClient"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(target[0]), target[1])


def __dir__() -> list[str]:
    return sorted(__all__)
