"""The filter algebra, and what each built-in actually matches."""

from __future__ import annotations

import pytest

from tdpy.api import types
from tdpy.dispatch.context import Context
from tdpy.dispatch.filters import (
    And,
    Command,
    ContentType,
    Filter,
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
    message_text,
    sender_id,
)


class Const(Filter):
    """A filter with a fixed answer, which records that it was consulted."""

    def __init__(self, value: bool, log: list[str] | None = None, name: str = "c") -> None:
        self.value = value
        self.log = log
        self.name = name

    async def check(self, event, ctx) -> bool:
        if self.log is not None:
            self.log.append(self.name)
        return self.value


TRUE, FALSE = Const(True), Const(False)


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
async def test_and_truth_table(left: bool, right: bool, expected: bool, ctx: Context) -> None:
    assert await (Const(left) & Const(right)).check(None, ctx) is expected


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [(True, True, True), (True, False, True), (False, True, True), (False, False, False)],
)
async def test_or_truth_table(left: bool, right: bool, expected: bool, ctx: Context) -> None:
    assert await (Const(left) | Const(right)).check(None, ctx) is expected


@pytest.mark.parametrize("value", [True, False])
async def test_not_inverts(value: bool, ctx: Context) -> None:
    assert await (~Const(value)).check(None, ctx) is (not value)


async def test_and_short_circuits(ctx: Context) -> None:
    log: list[str] = []
    await (Const(False, log, "first") & Const(True, log, "second")).check(None, ctx)

    assert log == ["first"], "the second part must not be consulted"


async def test_or_short_circuits(ctx: Context) -> None:
    log: list[str] = []
    await (Const(True, log, "first") | Const(False, log, "second")).check(None, ctx)

    assert log == ["first"]


async def test_operators_nest(ctx: Context) -> None:
    combined = (TRUE & FALSE) | (~FALSE & TRUE)

    assert await combined.check(None, ctx) is True


async def test_plain_callables_compose(ctx: Context, make_message) -> None:
    combined = Text() & (lambda event, _ctx: event.chat_id == 100)

    assert await combined.check(make_message("hi"), ctx) is True


async def test_async_callables_compose(ctx: Context, make_message) -> None:
    async def is_short(event, _ctx) -> bool:
        return len(message_text(event) or "") < 5

    assert await as_filter(is_short).check(make_message("hi"), ctx) is True
    assert await as_filter(is_short).check(make_message("a longer one"), ctx) is False


async def test_callable_on_the_left_still_composes(ctx: Context) -> None:
    combined = (lambda _event, _ctx: True) & TRUE

    assert await combined.check(None, ctx) is True


def test_a_non_callable_is_rejected() -> None:
    with pytest.raises(TypeError, match="neither a Filter nor callable"):
        as_filter(42)  # type: ignore[arg-type]


async def test_never_matches_nothing(ctx: Context, make_message) -> None:
    assert await Never().check(make_message("hi"), ctx) is False


def test_repr_reads_like_the_expression() -> None:
    assert repr(Text("a") & ~InChat(1)) == "(Text('a') & ~InChat([1]))"
    assert repr(Or(Text("a"), Text("b"))) == "(Text('a') | Text('b'))"
    assert repr(And(Never(), Not(Never()))) == "(Never() & ~Never())"


# ---- built-in filters --------------------------------------------------


@pytest.mark.parametrize(
    ("values", "text", "expected"),
    [((), "anything", True), (("hi",), "hi", True), (("hi",), "bye", False)],
)
async def test_text_matching(values, text, expected, ctx, make_message) -> None:
    assert await Text(*values).check(make_message(text), ctx) is expected


async def test_text_does_not_match_a_photo(ctx: Context, make_message) -> None:
    assert await Text().check(make_message(None), ctx) is False


async def test_text_can_ignore_case_and_match_prefixes(ctx: Context, make_message) -> None:
    assert await Text("HI", ignore_case=True).check(make_message("hi"), ctx) is True
    assert await Text("he", startswith=True).check(make_message("hello"), ctx) is True
    assert await Text("he", startswith=True).check(make_message("well hello"), ctx) is False


async def test_a_caption_counts_as_text(ctx: Context, make_message) -> None:
    # A photo with a caption should still match a command or a regex.
    photo = types.MessagePhoto(photo=types.Photo(), caption=types.FormattedText(text="/start now"))
    event = make_message(content=photo)

    assert await Command("start").check(event, ctx) is True


async def test_command_parses_name_and_arguments(ctx: Context, make_message) -> None:
    assert await Command("ban").check(make_message("/ban 42 spam"), ctx) is True

    parsed = ctx.data["command"]
    assert (parsed.name, parsed.prefix, parsed.args) == ("ban", "/", "42 spam")


async def test_command_accepts_any_registered_name(ctx: Context, make_message) -> None:
    accepts = Command("start", "help")

    assert await accepts.check(make_message("/help"), ctx) is True
    assert await accepts.check(make_message("/other"), ctx) is False


async def test_bare_command_filter_matches_any_command(ctx: Context, make_message) -> None:
    assert await Command().check(make_message("/whatever"), ctx) is True
    assert await Command().check(make_message("not a command"), ctx) is False


async def test_command_strips_the_bot_mention(ctx: Context, make_message) -> None:
    # Telegram appends @botname in groups; a handler for /start must still fire.
    assert await Command("start").check(make_message("/start@mybot"), ctx) is True
    assert ctx.data["command"].name == "start"


async def test_command_can_reject_another_bots_mention(ctx: Context, make_message) -> None:
    mine = Command("start", username="mybot")

    assert await mine.check(make_message("/start@mybot"), ctx) is True
    assert await mine.check(make_message("/start@otherbot"), ctx) is False
    assert await mine.check(make_message("/start"), ctx) is True


async def test_command_honours_custom_prefixes(ctx: Context, make_message) -> None:
    bang = Command("kick", prefixes="!")

    assert await bang.check(make_message("!kick"), ctx) is True
    assert await bang.check(make_message("/kick"), ctx) is False


async def test_regex_leaves_the_match_behind(ctx: Context, make_message) -> None:
    assert await Regex(r"order #(\d+)").check(make_message("order #42 please"), ctx) is True
    assert ctx.data["match"].group(1) == "42"


async def test_regex_can_anchor(ctx: Context, make_message) -> None:
    anchored = Regex(r"hi", search=False)

    assert await anchored.check(make_message("hi there"), ctx) is True
    assert await anchored.check(make_message("oh hi"), ctx) is False


async def test_from_user_matches_the_sender(ctx: Context, make_message) -> None:
    assert await FromUser(7).check(make_message("hi", user_id=7), ctx) is True
    assert await FromUser(7).check(make_message("hi", user_id=9), ctx) is False


async def test_a_channel_post_has_no_user(ctx: Context, make_message) -> None:
    # It carries a messageSenderChat; reading the chat id as a user id is how
    # a channel post ends up matching a per-user filter.
    post = make_message("hi", user_id=None, chat_id=-100123)

    assert sender_id(post) is None
    assert await FromUser(-100123).check(post, ctx) is False


async def test_in_chat_matches_the_chat(ctx: Context, make_message) -> None:
    assert await InChat(100).check(make_message("hi", chat_id=100), ctx) is True
    assert await InChat(100).check(make_message("hi", chat_id=200), ctx) is False


async def test_is_outgoing(ctx: Context, make_message) -> None:
    assert await IsOutgoing().check(make_message("hi", outgoing=True), ctx) is True
    assert await (~IsOutgoing()).check(make_message("hi"), ctx) is True


async def test_content_type_by_class_or_tag(ctx: Context, make_message) -> None:
    photo = make_message(None)

    assert await ContentType(types.MessagePhoto).check(photo, ctx) is True
    assert await ContentType("messagePhoto").check(photo, ctx) is True
    assert await ContentType(types.MessageText).check(photo, ctx) is False


async def test_state_filter_without_storage_matches_only_none(ctx: Context, make_message) -> None:
    assert await StateIs(None).check(make_message("hi"), ctx) is True
    assert await StateIs("Form:name").check(make_message("hi"), ctx) is False


async def test_an_empty_caption_is_not_text(ctx: Context, make_message) -> None:
    # TDLib's caption is non-nullable, so "no caption" arrives as an empty
    # FormattedText. Treating that as text makes Text() match every photo.
    photo = types.MessagePhoto(photo=types.Photo(), caption=types.FormattedText(text=""))

    assert message_text(make_message(content=photo)) is None
    assert await Text().check(make_message(content=photo), ctx) is False
