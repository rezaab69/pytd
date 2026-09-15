"""Parse modes, which run synchronously and so need no client round trip."""

from __future__ import annotations

import pytest

from tdpy._ffi import FakeTDJson
from tdpy.api import types
from tdpy.api.client import TypedClient
from tdpy.core.errors import BadRequest
from tdpy.helpers.text import HTML, MARKDOWN, MARKDOWN_V1, parse, resolve, text_content

PARSED = {
    "@type": "formattedText",
    "text": "bold",
    "entities": [
        {
            "@type": "textEntity",
            "offset": 0,
            "length": 4,
            "type": {"@type": "textEntityTypeBold"},
        }
    ],
}


def test_markdown_defaults_to_v2() -> None:
    # Version 2 is Telegram's MarkdownV2; 0 and 1 are the older dialect.
    assert MARKDOWN.version == 2
    assert MARKDOWN_V1.version == 1


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("markdown", MARKDOWN),
        ("MarkdownV2", MARKDOWN),
        ("html", HTML),
        ("HTML", HTML),
        ("markdown_v1", MARKDOWN_V1),
    ],
)
def test_names_resolve_to_modes(name: str, expected: types.TextParseMode) -> None:
    assert resolve(name) == expected


def test_a_mode_object_passes_through() -> None:
    assert resolve(HTML) is HTML


def test_an_unknown_name_lists_the_real_ones() -> None:
    # A typo here otherwise ships as unformatted text nobody notices.
    with pytest.raises(ValueError, match="markdown"):
        resolve("mrkdown")


def test_parsing_uses_execute_not_a_request(fake: FakeTDJson) -> None:
    fake.on_execute("parseTextEntities", PARSED)
    client = TypedClient(backend=fake)

    result = parse(client, "*bold*")

    assert isinstance(result, types.FormattedText)
    assert result.entities[0].type.type_name == "textEntityTypeBold"
    assert not fake.sent_of("parseTextEntities"), "it must not go through the request queue"


def test_parsing_works_before_the_client_starts(fake: FakeTDJson) -> None:
    fake.on_execute("parseTextEntities", PARSED)

    assert parse(TypedClient(backend=fake), "*bold*").text == "bold"


def test_the_parse_mode_reaches_tdlib(fake: FakeTDJson) -> None:
    seen: list[dict] = []

    def respond(request):
        seen.append(request)
        return PARSED

    fake.on_execute("parseTextEntities", respond)
    parse(TypedClient(backend=fake), "<b>bold</b>", "html")

    assert seen[0]["parse_mode"] == {"@type": "textParseModeHTML"}


def test_malformed_markup_raises(fake: FakeTDJson) -> None:
    # TDLib is strict about MarkdownV2; an unescaped '.' is an error.
    fake.on_execute(
        "parseTextEntities",
        {"@type": "error", "code": 400, "message": "Character '.' is reserved"},
    )

    with pytest.raises(BadRequest, match="reserved"):
        parse(TypedClient(backend=fake), "hello.", "markdown")


def test_content_without_a_parse_mode_is_literal(fake: FakeTDJson) -> None:
    # The safe default for anything user-supplied.
    content = text_content(TypedClient(backend=fake), "*not bold*")

    assert content.text.text == "*not bold*"
    assert content.text.entities == []
    assert not fake.sent_of("parseTextEntities")


def test_content_with_a_parse_mode_is_parsed(fake: FakeTDJson) -> None:
    fake.on_execute("parseTextEntities", PARSED)

    content = text_content(TypedClient(backend=fake), "*bold*", parse_mode="markdown")

    assert content.text.entities[0].length == 4


def test_content_carries_its_options(fake: FakeTDJson) -> None:
    content = text_content(
        TypedClient(backend=fake),
        "hi",
        link_preview_options=types.LinkPreviewOptions(is_disabled=True),
        clear_draft=False,
    )

    assert content.link_preview_options.is_disabled is True
    assert content.clear_draft is False
