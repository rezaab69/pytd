"""Predicates that decide whether a handler runs.

Filters compose with ``&``, ``|`` and ``~``, so a condition reads as one
expression instead of an opening pile of ``if`` statements::

    @router.on_message(Command("start") & ~IsOutgoing())

Every filter here is pure: it inspects the event and returns a verdict without
making a request. That keeps dispatch predictable and cheap, since a filter may
be evaluated against every update that arrives.

A filter may also record what it learned in ``ctx.data`` -- the parsed command,
the regex match -- so the handler does not have to redo the work.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any, TypeAlias

from tdpy.api import types
from tdpy.dispatch.context import Context
from tdpy.fsm.state import State

__all__ = [
    "And",
    "Command",
    "CommandInfo",
    "ContentType",
    "Filter",
    "FilterLike",
    "FromUser",
    "InChat",
    "IsOutgoing",
    "Never",
    "Not",
    "Or",
    "Regex",
    "StateIs",
    "Text",
    "all_of",
    "as_filter",
    "message_text",
    "sender_id",
]

#: A filter, or a plain callable that behaves like one.
FilterLike: TypeAlias = "Filter | Callable[[Any, Context], bool | Awaitable[bool]]"


class Filter(ABC):
    """A predicate over an event.

    Subclasses implement :meth:`check`. The operators build combinators rather
    than evaluating anything, so filters can be declared once at import time
    and reused across handlers.
    """

    @abstractmethod
    async def check(self, event: Any, ctx: Context) -> bool:
        """Whether the handler should run for this event."""

    def __and__(self, other: FilterLike) -> Filter:
        return And(self, as_filter(other))

    def __rand__(self, other: FilterLike) -> Filter:
        return And(as_filter(other), self)

    def __or__(self, other: FilterLike) -> Filter:
        return Or(self, as_filter(other))

    def __ror__(self, other: FilterLike) -> Filter:
        return Or(as_filter(other), self)

    def __invert__(self) -> Filter:
        return Not(self)

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


class _Callable(Filter):
    """Wraps a plain function so it composes like any other filter."""

    def __init__(self, function: Callable[[Any, Context], bool | Awaitable[bool]]) -> None:
        self.function = function

    async def check(self, event: Any, ctx: Context) -> bool:
        result = self.function(event, ctx)
        if isinstance(result, Awaitable):
            return bool(await result)
        return bool(result)

    def __repr__(self) -> str:
        return f"filter({getattr(self.function, '__name__', self.function)!r})"


def as_filter(value: FilterLike) -> Filter:
    """Coerce a filter or callable into a :class:`Filter`."""
    if isinstance(value, Filter):
        return value
    if callable(value):
        return _Callable(value)
    raise TypeError(f"{value!r} is neither a Filter nor callable")


class And(Filter):
    """All of its parts, short-circuiting on the first false."""

    def __init__(self, *parts: Filter) -> None:
        self.parts = parts

    async def check(self, event: Any, ctx: Context) -> bool:
        for part in self.parts:
            if not await part.check(event, ctx):
                return False
        return True

    def __repr__(self) -> str:
        return f"({' & '.join(map(repr, self.parts))})"


class Or(Filter):
    """Any of its parts, short-circuiting on the first true.

    A part that records something in ``ctx.data`` only does so if it is
    reached, which is the point of short-circuiting.
    """

    def __init__(self, *parts: Filter) -> None:
        self.parts = parts

    async def check(self, event: Any, ctx: Context) -> bool:
        for part in self.parts:
            if await part.check(event, ctx):
                return True
        return False

    def __repr__(self) -> str:
        return f"({' | '.join(map(repr, self.parts))})"


class Not(Filter):
    """The negation of one filter."""

    def __init__(self, part: Filter) -> None:
        self.part = part

    async def check(self, event: Any, ctx: Context) -> bool:
        return not await self.part.check(event, ctx)

    def __repr__(self) -> str:
        return f"~{self.part!r}"


class Never(Filter):
    """Matches nothing. Useful for disabling a handler without deleting it."""

    async def check(self, event: Any, ctx: Context) -> bool:
        return False


def message_text(event: Any) -> str | None:
    """The plain text of a message, or ``None`` if it has none.

    Captions count: a photo with a caption is text as far as a command or a
    regex is concerned, which is what users expect.
    """
    content = getattr(event, "content", None)
    if content is None:
        return None
    text = getattr(content, "text", None) or getattr(content, "caption", None)
    value = getattr(text, "text", None)
    # TDLib's caption field is not nullable: a photo with no caption carries an
    # empty FormattedText. Reporting that as text would make Text() match every
    # photo ever sent.
    return value or None


class Text(Filter):
    """Matches a message's text.

    Args:
        values: Exact strings to accept. With none given, any message that has
            text at all matches.
        ignore_case: Compare case-insensitively.
        startswith: Match a prefix instead of the whole string.
    """

    def __init__(
        self,
        *values: str,
        ignore_case: bool = False,
        startswith: bool = False,
    ) -> None:
        self.ignore_case = ignore_case
        self.startswith = startswith
        self.values = tuple(v.lower() if ignore_case else v for v in values)

    async def check(self, event: Any, ctx: Context) -> bool:
        text = message_text(event)
        if text is None:
            return False
        if not self.values:
            return True
        candidate = text.lower() if self.ignore_case else text
        if self.startswith:
            return any(candidate.startswith(value) for value in self.values)
        return candidate in self.values

    def __repr__(self) -> str:
        return f"Text({', '.join(map(repr, self.values))})"


class CommandInfo:
    """A parsed command, left in ``ctx.data['command']``."""

    __slots__ = ("args", "name", "prefix")

    def __init__(self, name: str, prefix: str, args: str) -> None:
        self.name = name
        self.prefix = prefix
        self.args = args

    def __repr__(self) -> str:
        return f"CommandInfo(name={self.name!r}, args={self.args!r})"


class Command(Filter):
    """Matches a bot command such as ``/start`` or ``/ban 42``.

    Telegram appends ``@botname`` when a command is used in a group, so that is
    stripped before comparing -- a handler for ``/start`` would otherwise miss
    every group mention of itself.

    The parsed :class:`CommandInfo` lands in ``ctx.data['command']``.

    Args:
        commands: Names to accept, without the prefix.
        prefixes: Characters that introduce a command.
        ignore_case: Compare names case-insensitively.
        username: This bot's username, used to accept ``/start@thisbot`` and
            reject ``/start@otherbot``. Without it, any mention is accepted.
    """

    def __init__(
        self,
        *commands: str,
        prefixes: str = "/",
        ignore_case: bool = True,
        username: str | None = None,
    ) -> None:
        self.commands = tuple(c.lower() if ignore_case else c for c in commands)
        self.prefixes = prefixes
        self.ignore_case = ignore_case
        self.username = username.lower().lstrip("@") if username else None

    async def check(self, event: Any, ctx: Context) -> bool:
        text = message_text(event)
        if not text or text[0] not in self.prefixes:
            return False

        head, _, args = text.partition(" ")
        name = head[1:]
        mention = None
        if "@" in name:
            name, _, mention = name.partition("@")
        if self.username is not None and mention and mention.lower() != self.username:
            return False

        compared = name.lower() if self.ignore_case else name
        if self.commands and compared not in self.commands:
            return False
        ctx.data["command"] = CommandInfo(name=name, prefix=head[0], args=args.strip())
        return True

    def __repr__(self) -> str:
        return f"Command({', '.join(map(repr, self.commands))})"


class Regex(Filter):
    """Matches a message's text against a pattern.

    The :class:`re.Match` lands in ``ctx.data['match']``, so the handler can
    read its groups without running the pattern a second time.

    Args:
        pattern: A pattern or compiled pattern.
        search: Search anywhere instead of anchoring at the start.
    """

    def __init__(self, pattern: str | re.Pattern[str], *, search: bool = True) -> None:
        self.pattern = re.compile(pattern) if isinstance(pattern, str) else pattern
        self.search = search

    async def check(self, event: Any, ctx: Context) -> bool:
        text = message_text(event)
        if text is None:
            return False
        match = self.pattern.search(text) if self.search else self.pattern.match(text)
        if match is None:
            return False
        ctx.data["match"] = match
        return True

    def __repr__(self) -> str:
        return f"Regex({self.pattern.pattern!r})"


def sender_id(event: Any) -> int | None:
    """The user id behind an event, if a user sent it.

    A channel post carries a ``messageSenderChat`` and has no user, so this is
    ``None`` rather than the chat id -- treating one as the other is how a
    channel post ends up matching a per-user filter.
    """
    sender = getattr(event, "sender_id", None)
    if isinstance(sender, types.MessageSenderUser):
        return sender.user_id
    identifier = getattr(event, "sender_user_id", None)
    return identifier if isinstance(identifier, int) else None


class FromUser(Filter):
    """Matches events sent by any of the given users."""

    def __init__(self, *user_ids: int) -> None:
        self.user_ids = frozenset(user_ids)

    async def check(self, event: Any, ctx: Context) -> bool:
        identifier = sender_id(event)
        return identifier is not None and identifier in self.user_ids

    def __repr__(self) -> str:
        return f"FromUser({sorted(self.user_ids)})"


class InChat(Filter):
    """Matches events in any of the given chats."""

    def __init__(self, *chat_ids: int) -> None:
        self.chat_ids = frozenset(chat_ids)

    async def check(self, event: Any, ctx: Context) -> bool:
        chat_id = getattr(event, "chat_id", None)
        return chat_id is not None and chat_id in self.chat_ids

    def __repr__(self) -> str:
        return f"InChat({sorted(self.chat_ids)})"


class IsOutgoing(Filter):
    """Matches messages this account sent.

    Usually worth negating: a user client that answers its own messages will
    answer itself forever.
    """

    async def check(self, event: Any, ctx: Context) -> bool:
        return bool(getattr(event, "is_outgoing", False))


class ContentType(Filter):
    """Matches a message by the kind of content it carries.

    Args:
        kinds: Generated content classes, or their TDLib tags as strings --
            ``ContentType(types.MessagePhoto)`` and
            ``ContentType("messagePhoto")`` are the same filter.
    """

    def __init__(self, *kinds: type | str) -> None:
        self.classes = tuple(k for k in kinds if isinstance(k, type))
        self.tags = frozenset(k for k in kinds if isinstance(k, str))

    async def check(self, event: Any, ctx: Context) -> bool:
        content = getattr(event, "content", None)
        if content is None:
            return False
        if self.classes and isinstance(content, self.classes):
            return True
        return getattr(content, "type_name", None) in self.tags

    def __repr__(self) -> str:
        names = [c.__name__ for c in self.classes] + sorted(self.tags)
        return f"ContentType({', '.join(names)})"


class StateIs(Filter):
    """Matches when the conversation is in one of the given states.

    ``StateIs(None)`` matches a conversation that has no state, which is how a
    handler says "only before the conversation starts".
    """

    def __init__(self, *states: State | str | None) -> None:
        self.states = frozenset(
            state.name if isinstance(state, State) else state for state in states
        )

    async def check(self, event: Any, ctx: Context) -> bool:
        if ctx.state is None:
            return None in self.states
        return await ctx.state.get_state() in self.states

    def __repr__(self) -> str:
        return f"StateIs({sorted(s for s in self.states if s is not None)})"


def all_of(filters: Iterable[FilterLike]) -> Filter | None:
    """Combine filters with ``and``, or return ``None`` when there are none."""
    parts: Sequence[Filter] = [as_filter(f) for f in filters]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return And(*parts)
