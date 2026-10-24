"""Echo bot: replies to every text message with the same text.

All configuration comes from a ``.env`` file in the directory you run this
from (exported variables of the same name take precedence)::

    TDJSON_PATH=/path/to/libtdjson.so
    TDPY_API_ID=1234567
    TDPY_API_HASH=0123456789abcdef0123456789abcdef
    TDPY_BOT_TOKEN=123456:ABC-DEF

Then::

    python examples/echo_bot.py

Stop it with Ctrl+C.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from pathlib import Path
from typing import Any

from tdpy import TypedClient
from tdpy.api import types
from tdpy.auth import EnvCredentials, TdlibSettings, authorize
from tdpy.dispatch import Context, Dispatcher, Router
from tdpy.dispatch.filters import Command, IsOutgoing, Text, message_text
from tdpy.helpers import reply_text

log = logging.getLogger("echo_bot")
router = Router()

# TDLib delivers the bot's own replies as new messages too. Without this gate
# the bot would echo its echoes forever.
INCOMING = ~IsOutgoing()


@router.on_message(Command("start") & INCOMING)
async def start(message: types.Message, ctx: Context) -> None:
    await reply_text(ctx.client, message, "Send me any text and I'll send it back.")


@router.on_message(Text() & INCOMING)
async def echo(message: types.Message, ctx: Context) -> None:
    await reply_text(ctx.client, message, message_text(message) or "")


def report(error: BaseException, event: Any, ctx: Context) -> None:
    """A plain function: the dispatcher calls it without awaiting."""
    log.error("handler failed on %s", type(event).__name__, exc_info=error)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # TDPY_API_ID / TDPY_API_HASH, and TDPY_BOT_TOKEN, from .env or the environment.
    # TDJSON_PATH is read the same way when TypedClient loads the library.
    settings = TdlibSettings.from_env(database_directory=Path("echo_bot_session"))
    credentials = EnvCredentials()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # unavailable on Windows
            loop.add_signal_handler(signum, stop.set)

    # update_queue_size=1: the dispatcher observes updates and nothing reads
    # client.updates(), whose buffer would otherwise grow without limit.
    async with TypedClient(update_queue_size=1) as client:
        dispatcher = Dispatcher(router, on_error=report)
        # Dispatch before logging in, so nothing delivered right after login is missed.
        async with dispatcher.run(client):
            await authorize(client, settings, credentials)
            await client.set_commands(
                commands=[types.BotCommand(command="start", description="How to use me")]
            )
            me = await client.get_me()
            log.info("echo bot running as user id %s", me.id)
            await stop.wait()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):  # Ctrl+C on Windows
        asyncio.run(main())
