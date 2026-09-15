"""Turning marked-up text into the entities TDLib wants.

``parseTextEntities`` is one of the 28 functions TDLib runs synchronously, so
formatting a message costs no round trip and works before the client has even
started.
"""

from __future__ import annotations

from typing import TypeAlias

from tdpy.api import functions, types
from tdpy.api.base import convert, to_builtins
from tdpy.core.client import Client

__all__ = ["HTML", "MARKDOWN", "MARKDOWN_V1", "ParseMode", "parse", "text_content"]

#: Telegram's MarkdownV2, the current dialect.
MARKDOWN = types.TextParseModeMarkdown(version=2)

#: The older Bot API "Markdown". Kept for text written against it.
MARKDOWN_V1 = types.TextParseModeMarkdown(version=1)

#: Telegram's HTML subset.
HTML = types.TextParseModeHTML()

#: A parse mode, or the name of one.
ParseMode: TypeAlias = "types.TextParseMode | str"

_BY_NAME: dict[str, types.TextParseMode] = {
    "markdown": MARKDOWN,
    "markdownv2": MARKDOWN,
    "markdown_v1": MARKDOWN_V1,
    "html": HTML,
}


def resolve(parse_mode: ParseMode) -> types.TextParseMode:
    """Accept either a parse mode or its name.

    Raises:
        ValueError: naming the modes that exist, since a typo here otherwise
            surfaces as unformatted text nobody notices until it ships.
    """
    if isinstance(parse_mode, str):
        try:
            return _BY_NAME[parse_mode.lower().replace("-", "_")]
        except KeyError:
            raise ValueError(
                f"unknown parse mode {parse_mode!r}; expected one of {sorted(_BY_NAME)}"
            ) from None
    return parse_mode


def parse(client: Client, text: str, parse_mode: ParseMode = MARKDOWN) -> types.FormattedText:
    """Parse ``text`` into a :class:`~tdpy.api.types.FormattedText`.

    Runs in the calling thread -- no request, no await.

    Raises:
        TDLibError: if the markup is malformed. TDLib is strict about
            MarkdownV2, where an unescaped ``.`` or ``-`` is an error.
    """
    request = functions.ParseTextEntities(text=text, parse_mode=resolve(parse_mode))
    payload = client.execute(to_builtins(request))
    result: types.FormattedText = convert(payload, types.FormattedText)
    return result


def text_content(
    client: Client,
    text: str,
    *,
    parse_mode: ParseMode | None = None,
    link_preview_options: types.LinkPreviewOptions | None = None,
    clear_draft: bool = True,
) -> types.InputMessageText:
    """Build message content from ``text``.

    Args:
        client: Used only to parse, and only when ``parse_mode`` is given.
        text: The message body.
        parse_mode: Markup dialect. ``None`` sends the text exactly as written,
            which is the safe choice for anything user-supplied.
        link_preview_options: How to preview links, if at all.
        clear_draft: Clear the chat's saved draft on send.
    """
    formatted = (
        parse(client, text, parse_mode)
        if parse_mode is not None
        else types.FormattedText(text=text)
    )
    return types.InputMessageText(
        text=formatted,
        link_preview_options=link_preview_options,
        clear_draft=clear_draft,
    )
