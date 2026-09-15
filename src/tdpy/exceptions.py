"""Exception hierarchy shared by every layer of the framework."""

from __future__ import annotations

__all__ = ["TdpyError"]


class TdpyError(Exception):
    """Base class for every exception raised by tdpy.

    Catching this catches framework problems (missing library, dead receiver,
    protocol violations) *and* errors returned by TDLib itself.
    """
