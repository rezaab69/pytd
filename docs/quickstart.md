# Quickstart

## Before you start

You need two things tdpy cannot supply.

**A `tdjson` shared library.** TDLib publishes no official Python wheels, so it
has to be installed separately — see [installing-tdlib.md](installing-tdlib.md).

**An `api_id` and `api_hash`** from <https://my.telegram.org/apps>. They
identify your *application*, not your account, and TDLib refuses to start
without them.

Put them in a `.env` file in the directory you run your application from.
tdpy reads it automatically; copy [`.env.example`](../.env.example) to start:

```ini
TDJSON_PATH=/path/to/libtdjson.so
TDPY_API_ID=1234567
TDPY_API_HASH=0123456789abcdef0123456789abcdef
TDPY_BOT_TOKEN=123456:ABC-DEF
```

Keep `.env` out of version control. Exported variables of the same name still
work and take precedence over the file, and `TDPY_ENV_FILE` points tdpy at a
file somewhere else.

## Install

```bash
pip install tdpy
```

## Say hello

```python
import asyncio

from tdpy import TypedClient
from tdpy.auth import ConsoleCredentials, TdlibSettings, authorize
from tdpy.helpers import send_text


async def main() -> None:
    async with TypedClient() as client:
        await authorize(client, TdlibSettings.from_env(), ConsoleCredentials())

        me = await client.get_me()
        print(f"signed in as {me.first_name}")

        await send_text(client, me.id, "Hello from tdpy")


asyncio.run(main())
```

The first run prompts for a phone number and the login code; after that the
session is in `td_session/` and start-up is silent. **Deleting that directory
logs the session out.**

## Answer messages

```python
import asyncio

from tdpy import TypedClient
from tdpy.auth import BotCredentials, TdlibSettings, authorize
from tdpy.dispatch import Dispatcher, Router
from tdpy.dispatch.filters import Command, IsOutgoing
from tdpy.helpers import reply_text

router = Router()


@router.on_message(Command("ping") & ~IsOutgoing())
async def ping(message, ctx):
    await reply_text(ctx.client, message, "pong")


async def main() -> None:
    async with TypedClient() as client:
        await authorize(client, TdlibSettings.from_env(), BotCredentials("123:ABC"))
        async with Dispatcher(router).run(client):
            await asyncio.Event().wait()   # until cancelled


asyncio.run(main())
```

## Which client

`TypedClient` returns generated objects, so `me.first_name` is checked by your
type checker. `Client` speaks plain dicts and is what `TypedClient` is built
on; reach for it when you want the raw wire format.

```python
from tdpy import Client

async with Client() as client:
    me = await client.invoke({"@type": "getMe"})
    print(me["first_name"])
```

## Where things live

| You want to | Look at |
|---|---|
| Build a bot (guide written for AI agents) | [ai-bot-guide.md](ai-bot-guide.md) |
| Log in | [auth.md](auth.md) |
| Route updates to handlers | [dispatch.md](dispatch.md) |
| Find a TDLib method | `tdpy.api.methods.Methods`, or [TDLib's docs](https://core.telegram.org/tdlib/docs/) |
| Understand the layering | [architecture.md](architecture.md) |

## Two things that surprise people

**`send_text` returns the message TDLib *created*, not the one it delivered.**
Its `sending_state` is still pending and its id is temporary. The confirmed
message arrives later as `updateMessageSendSucceeded`.

**Chat, user and message ids are `int53`** — ordinary Python ints. `Int64`
shows up for things like `custom_emoji_id` and `sticker.set_id`, which TDLib
sends as JSON *strings* to keep their precision.
