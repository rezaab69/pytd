"""Checks over the generated API as a whole.

The decoder-construction test is the important one: msgspec resolves a struct's
annotations lazily, so a class that imports fine can still be undecodable. That
is how a field named ``list`` -- which really exists on ``chatActiveStories`` --
silently broke three types until this test caught it.
"""

from __future__ import annotations

import msgspec
import pytest

from tdpy.api import base, functions, methods, types
from tdpy.api.base import Int64

ALL_TYPES = [
    value
    for value in vars(types).values()
    if isinstance(value, type) and issubclass(value, base.TDObject) and value is not base.TDObject
]
ALL_FUNCTIONS = [
    value
    for value in vars(functions).values()
    if isinstance(value, type) and issubclass(value, base.TDObject) and value is not base.TDObject
]
ALL_UNIONS = [
    (name, value)
    for name, value in vars(types).items()
    if not name.startswith("_") and not isinstance(value, type) and hasattr(value, "__args__")
]


def test_the_expected_number_of_symbols_was_generated() -> None:
    assert len(ALL_TYPES) == 2183
    assert len(ALL_UNIONS) == 211
    assert sum(1 for n in dir(methods.Methods) if not n.startswith("_")) == 1022
    assert sum(1 for n in dir(methods.SyncMethods) if not n.startswith("_")) == 28


def test_every_type_can_build_a_decoder() -> None:
    failures = []
    for struct in ALL_TYPES:
        try:
            msgspec.json.Decoder(struct, dec_hook=base.dec_hook)
        except Exception as exc:
            failures.append(f"{struct.__name__}: {exc}")
    assert not failures, f"{len(failures)} undecodable types: {failures[:5]}"


def test_every_function_can_build_a_decoder() -> None:
    failures = []
    for struct in ALL_FUNCTIONS:
        try:
            msgspec.json.Decoder(struct, dec_hook=base.dec_hook)
        except Exception as exc:
            failures.append(f"{struct.__name__}: {exc}")
    assert not failures, f"{len(failures)} undecodable functions: {failures[:5]}"


def test_every_union_can_build_a_decoder() -> None:
    failures = []
    for name, union in ALL_UNIONS:
        try:
            msgspec.json.Decoder(union, dec_hook=base.dec_hook)
        except Exception as exc:
            failures.append(f"{name}: {exc}")
    assert not failures, f"{len(failures)} undecodable unions: {failures[:5]}"


def test_fully_defaulted_types_round_trip() -> None:
    """Build, encode and decode every type that needs no arguments."""
    checked = 0
    failures = []
    for struct in ALL_TYPES:
        try:
            instance = struct()
        except TypeError:
            continue  # has required fields; covered by the decoder test above
        try:
            assert base.decode(base.encode(instance), struct) == instance
            checked += 1
        except Exception as exc:
            failures.append(f"{struct.__name__}: {exc}")
    assert not failures, f"{len(failures)} failed to round-trip: {failures[:5]}"
    assert checked > 1500, f"only {checked} types were exercised"


def test_the_shadowed_list_field_actually_works() -> None:
    # The regression that motivated this file. `list` shadows the builtin for
    # the rest of the class body, so `stories: list[StoryInfo]` needed
    # qualifying as `builtins.list`.
    decoded = base.decode(
        b'{"@type":"chatActiveStories","chat_id":7,"stories":[{"@type":"storyInfo"}]}',
        types.ChatActiveStories,
    )

    assert decoded.chat_id == 7
    assert len(decoded.stories) == 1
    assert decoded.list is None


def test_int64_survives_the_string_encoding() -> None:
    # TDLib reserves int64 for values that do not fit a double, such as custom
    # emoji ids. As a JSON number this would lose precision, which is exactly
    # why TDLib quotes it.
    big = 5789154169261340690
    decoded = base.decode(
        b'{"@type":"stickerFullTypeCustomEmoji","custom_emoji_id":"5789154169261340690"}',
        types.StickerFullTypeCustomEmoji,
    )

    assert decoded.custom_emoji_id == big
    assert isinstance(decoded.custom_emoji_id, Int64)
    assert b'"custom_emoji_id":"5789154169261340690"' in base.encode(decoded)


def test_chat_identifiers_are_int53_not_int64() -> None:
    # Chat, user and message ids are int53 in the schema: they fit in a double,
    # so TDLib sends them unquoted and they arrive as plain ints.
    decoded = base.decode(
        b'{"@type":"messageSenderUser","user_id":777000}', types.MessageSenderUser
    )

    assert decoded.user_id == 777000
    assert not isinstance(decoded.user_id, Int64)


def test_int53_stays_a_plain_json_number() -> None:
    # int53 fits in a double, so TDLib sends it unquoted and we must not quote it.
    request = functions.GetChatHistory(chat_id=777, from_message_id=1, limit=10)

    assert b'"chat_id":777' in base.encode(request)


def test_a_nested_union_decodes_by_its_tag() -> None:
    decoded = base.decode(
        b'{"@type":"message","id":1,"sender_id":{"@type":"messageSenderUser","user_id":5},'
        b'"content":{"@type":"messageText","text":{"@type":"formattedText","text":"hi"}}}',
        types.Message,
    )

    assert isinstance(decoded.content, types.MessageText)
    assert isinstance(decoded.sender_id, types.MessageSenderUser)
    assert decoded.content.text.text == "hi"


def test_a_truncated_payload_is_rejected() -> None:
    # Non-nullable object fields stay required, so a reply missing one is a
    # loud error rather than a None that surfaces three call frames later.
    import msgspec as _msgspec

    with pytest.raises(_msgspec.ValidationError, match="sender_id"):
        base.decode(b'{"@type":"message","id":1}', types.Message)


def test_unknown_fields_from_a_newer_tdlib_are_ignored() -> None:
    # Forward compatibility: TDLib adds fields constantly, and an added field
    # must not break a client built against an older schema.
    decoded = base.decode(
        b'{"@type":"ok","@extra":"tdpy-1","@client_id":3,"field_from_the_future":1}',
        types.Ok,
    )

    assert isinstance(decoded, types.Ok)


def test_optional_arguments_stay_off_the_wire() -> None:
    # sendMessage takes six arguments, four of them optional. Sending nulls
    # for the unset ones would be noise at best.
    request = functions.SendMessage(chat_id=1, input_message_content=functions_input_text("hello"))
    encoded = base.encode(request)

    assert b"reply_markup" not in encoded
    assert b"options" not in encoded
    assert b'"chat_id":1' in encoded


def functions_input_text(text: str) -> types.InputMessageText:
    return types.InputMessageText(text=types.FormattedText(text=text))


@pytest.mark.parametrize(
    ("method", "expected"),
    [("send_message", True), ("get_me", True), ("parse_text_entities", True)],
)
def test_expected_methods_exist(method: str, expected: bool) -> None:
    assert hasattr(methods.Methods, method) is expected
