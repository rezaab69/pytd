# tdpy

A complete async Python framework for Telegram's [TDLib](https://core.telegram.org/tdlib).

> **Status: alpha.** The framework is feature-complete against the plan:
> FFI foundation, async core, generated typed API, authorization, dispatch,
> helpers, packaging and docs. Bundled TDLib binaries are deliberately not
> included -- see [Installing TDLib](docs/installing-tdlib.md).

## Requirements

- Python 3.10+
- A `tdjson` shared library on the machine (`tdjson.dll`, `libtdjson.so`, or
  `libtdjson.dylib`). TDLib publishes no official Python wheels; see
  [docs/installing-tdlib.md](docs/installing-tdlib.md).
- An `api_id` / `api_hash` pair from https://my.telegram.org/apps.

## Configuration

Keep settings in a `.env` file in the directory your application runs from;
tdpy reads it automatically. Start from [`.env.example`](.env.example):

```ini
TDJSON_PATH=/path/to/libtdjson.so
TDPY_API_ID=1234567
TDPY_API_HASH=0123456789abcdef0123456789abcdef
TDPY_BOT_TOKEN=123456:ABC-DEF
```

| Variable | Read by | Meaning |
|---|---|---|
| `TDJSON_PATH` | the library loader | The `tdjson` library file, or a directory containing it |
| `TDPY_API_ID`, `TDPY_API_HASH` | `TdlibSettings.from_env()` | From https://my.telegram.org/apps |
| `TDPY_DATABASE_DIRECTORY` | `TdlibSettings.from_env()` | Optional; defaults to `td_session` |
| `TDPY_BOT_TOKEN` | `EnvCredentials` | From @BotFather |
| `TDPY_PHONE`, `TDPY_PASSWORD` | `EnvCredentials` | For user accounts instead of a bot |

A variable exported in the shell overrides the file, which keeps CI and
one-off runs working without editing it. `TDPY_ENV_FILE` points tdpy at a file
elsewhere; a missing file is not an error. `.env` is git-ignored — never commit
it.

[`examples/echo_bot.py`](examples/echo_bot.py) is a complete echo bot configured
entirely from this file: `python examples/echo_bot.py`.

## Logging in

`authorize` answers TDLib's `updateAuthorizationState` until the session is
ready, covering phone, bot token and QR login, plus 2FA, email and new-account
registration. Where the answers come from is up to you:

```python
from tdpy.auth import BotCredentials, ConsoleCredentials, EnvCredentials

await authorize(client, settings, ConsoleCredentials())        # prompts
await authorize(client, settings, BotCredentials(token))       # bots
await authorize(client, settings, EnvCredentials())            # unattended
```

`CallbackCredentials` builds one from your own async functions, for GUIs and
web apps. The flow *observes* the update stream rather than consuming it, so an
application already reading `client.updates()` loses nothing while it runs.

## Handling updates

Routers nest, filters compose, and the first matching handler wins:

```python
from tdpy.dispatch import Dispatcher, Router
from tdpy.dispatch.filters import Command, IsOutgoing, Regex

router = Router()


@router.on_message(Command("start") & ~IsOutgoing())
async def start(message, ctx):
    await ctx.client.send_message(
        chat_id=message.chat_id,
        input_message_content=types.InputMessageText(
            text=types.FormattedText(text="Hello!")
        ),
    )


@router.on_message(Regex(r"order #(\d+)"))
async def order(message, ctx):
    number = ctx.data["match"].group(1)      # the filter already matched it


async with Dispatcher(router).run(client):
    await stop_event.wait()
```

Multi-step conversations get state, in memory or in Redis:

```python
from tdpy.fsm import MemoryStorage, State, StatesGroup


class Form(StatesGroup):
    name = State()


@router.on_message(Command("register"), StateIs(None))
async def begin(message, ctx):
    await ctx.fsm.set_state(Form.name)


@router.on_message(StateIs(Form.name))
async def got_name(message, ctx):
    await ctx.fsm.update_data(name=message.content.text.text)
    await ctx.fsm.clear()


dispatcher = Dispatcher(router, storage=MemoryStorage())
```

Updates are handled **in order** by default (one worker), because Telegram's
ordering carries meaning -- an edit follows its message, a deletion follows
both. Pass `workers=N` when handlers are slow and independent.

## Helpers

Short forms over the generated API, for the things every client does.

```python
from tdpy.helpers import download, iter_history, reply_text, send_text

await send_text(client, chat_id, "*bold*", parse_mode="markdown")
await reply_text(ctx.client, message, "pong")

# Paging. A short page is not the end of history -- only an empty one is,
# because TDLib chooses page sizes for itself.
async for message in iter_history(client, chat_id, limit=200):
    ...

# Files, with progress.
file = await download(client, file_id, progress=lambda done, total: ...)
print(file.local.path)
```

Parse modes run through TDLib's *synchronous* `parseTextEntities`, so
formatting costs no round trip and works before the client has started. Text is
sent literally unless you ask for a parse mode, which is the safe default for
anything a user typed.

## Layout

| Path | Role |
|---|---|
| [src/tdpy/_ffi/](src/tdpy/_ffi/) | The only code that touches `ctypes` |
| [src/tdpy/_ffi/abi.py](src/tdpy/_ffi/abi.py) | The protocol every backend implements |
| [src/tdpy/_ffi/loader.py](src/tdpy/_ffi/loader.py) | Library discovery and `dlopen` |
| [src/tdpy/_ffi/tdjson.py](src/tdpy/_ffi/tdjson.py) | The real binding |
| [src/tdpy/_ffi/fake.py](src/tdpy/_ffi/fake.py) | In-process double, so CI needs no binary |
| [src/tdpy/core/receiver.py](src/tdpy/core/receiver.py) | The one thread allowed to call `td_receive` |
| [src/tdpy/core/client.py](src/tdpy/core/client.py) | asyncio client: `@extra` correlation, updates |
| [src/tdpy/core/errors.py](src/tdpy/core/errors.py) | `TDLibError` hierarchy, including `FloodWait` |
| [src/tdpy/core/retry.py](src/tdpy/core/retry.py) | Opt-in retry policy |
| [src/tdpy/api/base.py](src/tdpy/api/base.py) | Hand-written floor: `Int64`, `TDObject`, codecs |
| [src/tdpy/api/types.py](src/tdpy/api/types.py) | **Generated** -- 2183 structs, 211 union aliases |
| [src/tdpy/api/functions.py](src/tdpy/api/functions.py) | **Generated** -- 1022 request objects |
| [src/tdpy/api/methods.py](src/tdpy/api/methods.py) | **Generated** -- 1022 typed coroutines |
| [src/tdpy/codegen/](src/tdpy/codegen/) | The generator and its drift gate |
| [src/tdpy/auth/flow.py](src/tdpy/auth/flow.py) | The 13-state `authorizationState` machine |
| [src/tdpy/auth/credentials.py](src/tdpy/auth/credentials.py) | Where codes and passwords come from |
| [src/tdpy/auth/settings.py](src/tdpy/auth/settings.py) | `setTdlibParameters` |
| [src/tdpy/dispatch/router.py](src/tdpy/dispatch/router.py) | Nestable routers, first match wins |
| [src/tdpy/dispatch/filters.py](src/tdpy/dispatch/filters.py) | Composable predicates (`&`, `\|`, `~`) |
| [src/tdpy/dispatch/dispatcher.py](src/tdpy/dispatch/dispatcher.py) | Updates in, handlers out |
| [src/tdpy/fsm/](src/tdpy/fsm/) | Conversation state, memory or Redis |
| [src/tdpy/helpers/](src/tdpy/helpers/) | Files, parse modes, paging, common sends |

## Usage so far

```python
import asyncio
from tdpy.core import Client


async def main() -> None:
    async with Client() as client:
        me = await client.invoke({"@type": "getMe"})
        print(me["first_name"])

        async for update in client.updates():
            print(update["@type"])


asyncio.run(main())
```

Or typed and logged in, which is what you probably want:

```python
import asyncio
from tdpy import TypedClient
from tdpy.api import types
from tdpy.auth import ConsoleCredentials, TdlibSettings, authorize


async def main() -> None:
    async with TypedClient() as client:
        await authorize(client, TdlibSettings.from_env(), ConsoleCredentials())

        me = await client.get_me()
        print(me.first_name)          # checked and completed by your type checker

        await client.send_message(
            chat_id=me.id,
            input_message_content=types.InputMessageText(
                text=types.FormattedText(text="hello")
            ),
        )


asyncio.run(main())
```

The 28 functions TDLib can run without a request queue live on `.sync`, so that
`await client.<anything>` is always correct:

```python
entities = client.sync.get_text_entities(text="@durov")   # no client needed
```

Retries are off by default, because most TDLib methods are not idempotent:

```python
from tdpy.core import RetryPolicy

await client.invoke({"@type": "getChat", "chat_id": 1}, retry=RetryPolicy())
```

## Documentation

| Guide | |
|---|---|
| [Quickstart](docs/quickstart.md) | Install, sign in, send a message |
| [Building bots, for AI agents](docs/ai-bot-guide.md) | Hard rules, a tested reference bot, recipes, offline tests |
| [Logging in](docs/auth.md) | Phone, bot, QR, 2FA, sessions |
| [Routing updates](docs/dispatch.md) | Routers, filters, middleware, FSM |
| [Architecture](docs/architecture.md) | How the layers fit, and why |
| [Installing TDLib](docs/installing-tdlib.md) | Getting `tdjson` onto the machine |

## Development

```bash
uv sync
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy                              # includes tests/typing_checks.py
uv run pytest --cov=tdpy --cov-fail-under=90
uv run python -m tdpy.codegen --check    # generated API matches the schema
uv build
```

CI runs all of it on Python 3.10-3.14 across Linux, macOS and Windows. Type
checking runs on all three OSes because the library loader branches on
`sys.platform`. Tests needing a real TDLib are marked `requires_binary` and
skip themselves; an opt-in workflow builds TDLib and runs just those.

Tests marked `requires_binary` skip automatically when no TDLib is installed.

### Regenerating the API

`src/tdpy/api/{types,functions,methods}.py` are generated from
[tools/td_api.tl](tools/td_api.tl) and must not be edited by hand. After
updating the schema:

```bash
uv run python -m tdpy.codegen
```

Each generated file records the schema's sha256, and `--check` fails CI when
they disagree.
