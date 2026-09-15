"""Routing updates to handlers: routers, filters, middleware, dispatch."""

from __future__ import annotations

from tdpy.dispatch.context import Context
from tdpy.dispatch.dispatcher import Dispatcher, ErrorHandler
from tdpy.dispatch.filters import (
    And,
    Command,
    CommandInfo,
    ContentType,
    Filter,
    FilterLike,
    FromUser,
    InChat,
    IsOutgoing,
    Never,
    Not,
    Or,
    Regex,
    StateIs,
    Text,
    as_filter,
)
from tdpy.dispatch.middleware import Middleware, NextHandler
from tdpy.dispatch.router import Handler, HandlerFunction, Router, SkipHandler

__all__ = [
    "And",
    "Command",
    "CommandInfo",
    "ContentType",
    "Context",
    "Dispatcher",
    "ErrorHandler",
    "Filter",
    "FilterLike",
    "FromUser",
    "Handler",
    "HandlerFunction",
    "InChat",
    "IsOutgoing",
    "Middleware",
    "Never",
    "NextHandler",
    "Not",
    "Or",
    "Regex",
    "Router",
    "SkipHandler",
    "StateIs",
    "Text",
    "as_filter",
]
