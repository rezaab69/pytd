"""Short forms for the sends people actually make.

Everything here is a thin arrangement of the generated API. The raw call stays
available and stays the source of truth; this is only the shape that saves
writing three nested constructors to say "hello".
"""

from __future__ import annotations

from tdpy.api import functions, types
from tdpy.api.base import convert, to_builtins
from tdpy.core.client import Client
from tdpy.helpers.text import ParseMode, text_content

__all__ = ["reply_text", "send_text"]


async def send_text(
    client: Client,
    chat_id: int,
    text: str,
    *,
    parse_mode: ParseMode | None = None,
    reply_to: types.InputMessageReplyTo | None = None,
    disable_notification: bool = False,
    link_preview_options: types.LinkPreviewOptions | None = None,
    clear_draft: bool = True,
) -> types.Message:
    """Send a text message.

    Args:
        client: A started, authorized client.
        chat_id: Where to send it.
        text: The body.
        parse_mode: ``"markdown"``, ``"html"``, or a
            :class:`~tdpy.api.types.TextParseMode`. ``None`` sends the text
            literally, which is the safe choice for anything user-supplied.
        reply_to: The message being replied to.
        disable_notification: Deliver quietly.
        link_preview_options: How to preview links, if at all.
        clear_draft: Clear the chat's saved draft.

    Returns:
        The message as TDLib created it -- **not** as delivered. Its
        ``sending_state`` is still ``messageSendingStatePending`` and its id is
        temporary; the confirmed message arrives later as
        ``updateMessageSendSucceeded``.

    Raises:
        TDLibError: if TDLib rejected the send, malformed markup included.
    """
    request = functions.SendMessage(
        chat_id=chat_id,
        reply_to=reply_to,
        options=types.MessageSendOptions(disable_notification=disable_notification),
        input_message_content=text_content(
            client,
            text,
            parse_mode=parse_mode,
            link_preview_options=link_preview_options,
            clear_draft=clear_draft,
        ),
    )
    result: types.Message = convert(await client.invoke(to_builtins(request)), types.Message)
    return result


async def reply_text(
    client: Client,
    message: types.Message,
    text: str,
    **kwargs: object,
) -> types.Message:
    """Reply to ``message`` in its own chat.

    Example:
        >>> @router.on_message(Command("ping"))             # doctest: +SKIP
        ... async def ping(message, ctx):
        ...     await reply_text(ctx.client, message, "pong")
    """
    return await send_text(
        client,
        message.chat_id,
        text,
        reply_to=types.InputMessageReplyToMessage(message_id=message.id),
        **kwargs,  # type: ignore[arg-type]
    )
