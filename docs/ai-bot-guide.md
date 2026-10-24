# Building Telegram bots with tdpy — a guide for AI coding agents

This guide is for an AI agent writing a Telegram bot on **tdpy**. Read the hard
rules first, then copy the reference bot, then use the reference and recipes to
extend it. Every import path and signature here was checked against the code,
and every complete code block type-checks under `mypy --strict`.

**tdpy is not python-telegram-bot, aiogram, Pyrogram or Telethon.** The names
look similar in places and the APIs are not compatible. When this guide
disagrees with what you remember from those libraries, follow this guide.

Contents:

1. [Hard rules](#1-hard-rules)
2. [What tdpy is](#2-what-tdpy-is)
3. [Setup](#3-setup)
4. [The reference bot](#4-the-reference-bot)
5. [Testing the bot offline](#5-testing-the-bot-offline)
6. [API reference](#6-api-reference)
7. [Recipes](#7-recipes)
8. [Coming from the Bot API](#8-coming-from-the-bot-api)
9. [Symptoms and fixes](#9-symptoms-and-fixes)
10. [User accounts instead of bots](#10-user-accounts-instead-of-bots)
11. [Before you say you are done](#11-before-you-say-you-are-done)

---

## 1. Hard rules

Break one of these and the bot will misbehave in a way that is easy to miss.

1. **Ignore your own messages.** TDLib delivers the bot's *own* sent messages
   as `updateNewMessage` too — the schema says a new message "can also be an
   outgoing message". Any message handler that sends a message must be gated
   with `~IsOutgoing()`, or the bot answers its own answers forever.
2. **Handlers are `async def handler(event, ctx)` or `async def handler(event)`.**
   Any other number of positional parameters raises `TypeError` when the
   decorator runs. Synchronous functions are not accepted as handlers.
3. **The first matching handler wins**, in registration order, depth-first
   through nested routers. Register specific handlers (commands, conversation
   states) before general ones (any text).
4. **Use `TypedClient`.** Generated methods are the snake_case form of TDLib
   function names (`sendMessage` → `send_message`), take **keyword arguments
   only**, and are **all coroutines** — always `await` them. The 28 functions
   TDLib runs synchronously live on `client.sync` and are **not** awaited.
5. **`InlineKeyboardButton` and `KeyboardButton` require `style`.** Pass
   `style=types.ButtonStyleDefault()`. Leaving it out raises
   `TypeError: Missing required argument 'style'`.
6. **Callback data is `bytes`** — when you set it and when you read it. Compare
   with `b"..."` or `.decode()` it. Telegram caps it at 64 bytes.
7. **Always answer a callback query** with `answer_callback_query`, even with no
   text. Until you do, the user's app shows the button as loading.
8. **Build the client with `TypedClient(update_queue_size=1)` when a
   `Dispatcher` consumes updates.** The dispatcher observes updates; `Client`
   also buffers every update for `client.updates()`, unbounded by default.
   Nothing reads that buffer in a dispatcher-based bot, so it grows forever.
9. **Start the dispatcher before `authorize`.** Telegram delivers updates as
   soon as the login completes — for a bot, the messages it received while
   offline. A dispatcher started afterwards never sees them.
10. **One process per database directory.** A second process on the same
    directory fails with
    `BadRequest: [400] Can't lock file ".../td.binlog", because it is already in use`.
11. **`on_error` must be a plain function.** The dispatcher calls it without
    awaiting. An `async def` error handler silently never runs.
12. **A sent message's id is temporary.** `send_message` returns the message as
    TDLib created it, still pending. The server-confirmed message arrives later
    as `updateMessageSendSucceeded`, whose `old_message_id` is the temporary id.
13. **Never put credentials in code.** Read `TDPY_API_ID`, `TDPY_API_HASH` and
    `TDPY_BOT_TOKEN` from the environment or the application's `.env` file.
14. **Do not block the event loop in a handler.** One worker handles updates in
    order, so a `time.sleep` or a blocking HTTP call stalls the whole bot. Use
    async I/O, or `await asyncio.to_thread(...)` for blocking work.

---

## 2. What tdpy is

TDLib is Telegram's own client library. It speaks MTProto — the protocol the
official apps use — not the HTTP Bot API. A bot built on it is a TDLib client
that logged in with a bot token. Three consequences:

- **There are no webhooks and no `getUpdates`.** TDLib pushes updates to the
  client over its own connection.
- **TDLib keeps a local database.** The database directory *is* the session:
  keep it and restarts skip the login; delete it and the bot logs in again.
- **Identifiers and shapes differ from the Bot API.** See
  [section 8](#8-coming-from-the-bot-api).

tdpy wraps TDLib in layers. A bot uses the top three:

| Layer | Import from | What it gives you |
|---|---|---|
| Dispatch | `tdpy.dispatch`, `tdpy.fsm` | Routers, filters, middleware, conversation state |
| Helpers | `tdpy.helpers` | `send_text`, `reply_text`, `download`, parse modes, paging |
| Typed API | `tdpy`, `tdpy.api.types` | `TypedClient` with 1022 methods; 2183 generated types |
| Core | `tdpy.core` | `Client`, errors, `RetryPolicy` |

Every name a bot needs, in one place:

```python
# fragment -- the imports a bot draws on
from tdpy import TypedClient
from tdpy.api import types
from tdpy.api.base import Int64
from tdpy.auth import BotCredentials, ConsoleCredentials, EnvCredentials, TdlibSettings, authorize
from tdpy.core import BadRequest, FloodWait, RetryPolicy, TDLibError
from tdpy.dispatch import Context, Dispatcher, NextHandler, Router, SkipHandler
from tdpy.dispatch.filters import (
    Command,
    CommandInfo,
    ContentType,
    FromUser,
    InChat,
    IsOutgoing,
    Regex,
    StateIs,
    Text,
    as_filter,
    message_text,
    sender_id,
)
from tdpy.fsm import MemoryStorage, RedisStorage, State, StatesGroup
from tdpy.helpers import download, local_file, parse, reply_text, send_text, upload
```

---

## 3. Setup

You need three things tdpy cannot supply.

| Requirement | Where it comes from | How tdpy reads it |
|---|---|---|
| The `tdjson` shared library | Built from TDLib, or a prebuilt copy | `TDJSON_PATH` — the file, or a directory containing it |
| `api_id` and `api_hash` | <https://my.telegram.org/apps> | `TDPY_API_ID`, `TDPY_API_HASH` |
| A bot token | @BotFather | `TDPY_BOT_TOKEN` (the name `EnvCredentials` reads) |

Store all four in a `.env` file in the bot's working directory — the directory
you run `python bot.py` from, not the directory of the script — (tdpy reads it
automatically; start from `.env.example`, and never commit the real file):

```ini
TDJSON_PATH=/opt/td/libtdjson.so
TDPY_API_ID=1234567
TDPY_API_HASH=0123456789abcdef0123456789abcdef
TDPY_BOT_TOKEN=123456:ABC-DEF
```

An exported variable of the same name overrides the file, and `TDPY_ENV_FILE`
names a file elsewhere.

The library is `libtdjson.so` on Linux, `libtdjson.dylib` on macOS and
`tdjson.dll` on Windows. When `TDJSON_PATH` is set, tdpy tries that library
only; if it will not load, `TDJsonNotFoundError` lists the path and the
operating system's reason — for example `libc++.so.1: cannot open shared
object file`, which means the library needs LLVM's libc++ installed
(`apt-get install libc++1 libc++abi1` on Debian and Ubuntu).

tdpy lowers TDLib's own log to errors only and forwards it to the `tdpy.tdlib`
logger. Configure `logging` to see it.

---

## 4. The reference bot

A complete bot: `/start`, an inline-keyboard poll with callback answers, a
two-step conversation, an echo for everything else, command registration,
error reporting, and a clean shutdown. Save it as `bot.py`.

```python
"""Reference tdpy bot: commands, inline keyboard, conversation state, echo."""

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
from tdpy.dispatch.filters import Command, IsOutgoing, StateIs, Text, message_text
from tdpy.fsm import MemoryStorage, State, StatesGroup
from tdpy.helpers import reply_text

log = logging.getLogger("bot")
router = Router()

# TDLib also delivers the bot's own sent messages as updateNewMessage. Without
# this gate, any handler that replies will answer its own replies forever.
INCOMING = ~IsOutgoing()


class Signup(StatesGroup):
    name = State()


def api(ctx: Context) -> TypedClient:
    """Return the client with its generated methods visible to the type checker.

    ctx.client is annotated as the base Client. The bot runs on TypedClient.
    """
    if not isinstance(ctx.client, TypedClient):
        raise TypeError("start the bot with TypedClient")
    return ctx.client


def vote_keyboard() -> types.ReplyMarkupInlineKeyboard:
    def button(label: str, data: bytes) -> types.InlineKeyboardButton:
        return types.InlineKeyboardButton(
            text=label,
            style=types.ButtonStyleDefault(),  # required
            type=types.InlineKeyboardButtonTypeCallback(data=data),
        )

    return types.ReplyMarkupInlineKeyboard(
        rows=[[button("Yes", b"vote:yes"), button("No", b"vote:no")]]
    )


# The first matching handler wins, so specific handlers come first.


@router.on_message(Command("start") & INCOMING)
async def start(message: types.Message, ctx: Context) -> None:
    await reply_text(ctx.client, message, "Hi! Try /vote or /signup.")


@router.on_message(Command("vote") & INCOMING)
async def vote(message: types.Message, ctx: Context) -> None:
    await api(ctx).send_message(
        chat_id=message.chat_id,
        reply_markup=vote_keyboard(),
        input_message_content=types.InputMessageText(
            text=types.FormattedText(text="Do you like tdpy?")
        ),
    )


@router.on_callback_query()
async def on_vote(update: types.UpdateNewCallbackQuery, ctx: Context) -> None:
    payload = update.payload
    if not isinstance(payload, types.CallbackQueryPayloadData):
        return
    choice = payload.data.decode()  # callback data is bytes
    await api(ctx).answer_callback_query(
        callback_query_id=update.id, text=f"You chose {choice}"
    )


@router.on_message(Command("signup") & INCOMING, StateIs(None))
async def signup(message: types.Message, ctx: Context) -> None:
    await ctx.fsm.set_state(Signup.name)
    await reply_text(ctx.client, message, "What's your name?")


@router.on_message(StateIs(Signup.name) & Text() & INCOMING)
async def got_name(message: types.Message, ctx: Context) -> None:
    name = message_text(message) or ""
    await ctx.fsm.clear()
    await reply_text(ctx.client, message, f"Welcome, {name}!")


@router.on_message(Text() & INCOMING)
async def echo(message: types.Message, ctx: Context) -> None:
    await reply_text(ctx.client, message, message_text(message) or "")


def report(error: BaseException, event: Any, ctx: Context) -> None:
    """A plain function: the dispatcher calls it without awaiting."""
    log.error("handler failed on %s", type(event).__name__, exc_info=error)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = TdlibSettings.from_env(database_directory=Path("bot_session"))
    # Both read .env in the working directory, or exported variables.
    credentials = EnvCredentials()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # unavailable on Windows
            loop.add_signal_handler(signum, stop.set)

    # update_queue_size=1: the dispatcher observes updates and nothing reads
    # client.updates(), whose buffer would otherwise grow without limit.
    async with TypedClient(update_queue_size=1) as client:
        dispatcher = Dispatcher(router, storage=MemoryStorage(), on_error=report)
        # Dispatch before logging in, so updates delivered the moment the login
        # completes are not missed.
        async with dispatcher.run(client):
            await authorize(client, settings, credentials)
            await client.set_commands(
                commands=[
                    types.BotCommand(command="start", description="Say hello"),
                    types.BotCommand(command="vote", description="Answer a poll"),
                    types.BotCommand(command="signup", description="Tell me your name"),
                ]
            )
            me = await client.get_me()
            log.info("running as user id %s", me.id)
            await stop.wait()


if __name__ == "__main__":
    asyncio.run(main())
```

Run it with `python bot.py` from the directory holding your `.env`. Stop it with Ctrl+C; leaving
the `async with` blocks drains queued updates and closes TDLib cleanly, which
flushes its database.

---

## 5. Testing the bot offline

`FakeTDJson` is tdpy's in-process stand-in for TDLib. It needs no library, no
network and no account. Script replies with `fake.on(method, reply)`, drive
the bot with `Dispatcher.feed`, and read what the bot sent with
`fake.sent_of(method)` — requests exactly as they would go on the wire.

**`Dispatcher.feed` does not raise handler exceptions.** It hands them to
`on_error`, like live dispatch does. Collect them and assert there are none,
or a crashing handler passes the test.

Install `pytest` and `pytest-asyncio`, and enable auto mode:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

Save as `test_bot.py` next to `bot.py`:

```python
"""Offline tests for bot.py, against tdpy's in-process stand-in for TDLib."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

import pytest

import bot
from tdpy._ffi import FakeTDJson
from tdpy.api import types
from tdpy.api.base import Int64
from tdpy.api.client import TypedClient
from tdpy.dispatch import Context, Dispatcher
from tdpy.fsm import MemoryStorage

CHAT = 100
USER = 7
CLOSED = {
    "@type": "updateAuthorizationState",
    "authorization_state": {"@type": "authorizationStateClosed"},
}


def created_message(request: dict[str, Any]) -> dict[str, Any]:
    """What TDLib answers sendMessage with: at least the fields Message requires."""
    return {
        "@type": "message",
        "id": 1,
        "chat_id": request["chat_id"],
        "sender_id": {"@type": "messageSenderUser", "user_id": 1},
        "content": {"@type": "messageText", "text": {"@type": "formattedText", "text": ""}},
        "is_outgoing": True,
    }


def incoming(text: str, *, outgoing: bool = False) -> types.UpdateNewMessage:
    return types.UpdateNewMessage(
        message=types.Message(
            id=1,
            chat_id=CHAT,
            sender_id=types.MessageSenderUser(user_id=USER),
            content=types.MessageText(text=types.FormattedText(text=text)),
            is_outgoing=outgoing,
        )
    )


def sent_texts(fake: FakeTDJson) -> list[str]:
    return [r["input_message_content"]["text"]["text"] for r in fake.sent_of("sendMessage")]


@pytest.fixture
def fake() -> FakeTDJson:
    backend = FakeTDJson()
    backend.on("close", lambda _request: CLOSED)
    backend.on("sendMessage", created_message)
    backend.on("answerCallbackQuery", {"@type": "ok"})
    return backend


@pytest.fixture
async def client(fake: FakeTDJson) -> AsyncIterator[TypedClient]:
    async with TypedClient(backend=fake, poll_timeout=0.05) as instance:
        yield instance


@pytest.fixture
def errors() -> list[BaseException]:
    return []


@pytest.fixture
def dispatcher(errors: list[BaseException]) -> Dispatcher:
    def collect(error: BaseException, event: Any, ctx: Context) -> None:
        errors.append(error)

    return Dispatcher(bot.router, storage=MemoryStorage(), on_error=collect)


async def test_start_replies(
    client: TypedClient, fake: FakeTDJson, dispatcher: Dispatcher, errors: list[BaseException]
) -> None:
    await dispatcher.feed(client, incoming("/start"))

    assert errors == []
    assert sent_texts(fake) == ["Hi! Try /vote or /signup."]


async def test_own_messages_are_ignored(
    client: TypedClient, fake: FakeTDJson, dispatcher: Dispatcher, errors: list[BaseException]
) -> None:
    await dispatcher.feed(client, incoming("Hi! Try /vote or /signup.", outgoing=True))

    assert errors == []
    assert fake.sent_of("sendMessage") == []


async def test_vote_sends_callback_buttons(
    client: TypedClient, fake: FakeTDJson, dispatcher: Dispatcher, errors: list[BaseException]
) -> None:
    await dispatcher.feed(client, incoming("/vote"))

    assert errors == []
    first = fake.sent_of("sendMessage")[0]["reply_markup"]["rows"][0][0]
    assert first["type"]["@type"] == "inlineKeyboardButtonTypeCallback"
    assert base64.b64decode(first["type"]["data"]) == b"vote:yes"  # bytes travel as base64


async def test_callback_query_is_answered(
    client: TypedClient, fake: FakeTDJson, dispatcher: Dispatcher, errors: list[BaseException]
) -> None:
    update = types.UpdateNewCallbackQuery(
        id=Int64(5),
        sender_user_id=USER,
        chat_id=CHAT,
        message_id=1,
        payload=types.CallbackQueryPayloadData(data=b"vote:no"),
    )
    await dispatcher.feed(client, update)

    assert errors == []
    answer = fake.sent_of("answerCallbackQuery")[0]
    assert answer["callback_query_id"] == "5"  # int64 travels as a string
    assert answer["text"] == "You chose vote:no"


async def test_signup_conversation(
    client: TypedClient, fake: FakeTDJson, dispatcher: Dispatcher, errors: list[BaseException]
) -> None:
    await dispatcher.feed(client, incoming("/signup"))
    await dispatcher.feed(client, incoming("Ada"))

    assert errors == []
    assert sent_texts(fake) == ["What's your name?", "Welcome, Ada!"]
```

Patterns for scripting the fake:

```python
# fragment -- scripting FakeTDJson
from tdpy._ffi import FakeTDJson
from tdpy._ffi.fake import error

fake = FakeTDJson()
fake.on("getMe", {"@type": "user", "id": 42, "first_name": "Bot",
                  "status": {"@type": "userStatusEmpty"}, "type": {"@type": "userTypeBot"}})
fake.on("getChat", lambda request: error(400, "Chat not found"))  # script a failure
fake.on("sendMessage", lambda request: None)  # stay silent; push a reply yourself
fake.push({"@type": "updateOption", "name": "version"}, client_id=1)  # inject an update
fake.on_execute("parseTextEntities", {"@type": "formattedText", "text": "x"})  # client.sync.*
```

A scripted reply is decoded into the generated type, so it must carry that
type's required fields. `User` requires `status` and `type`; `Message` requires
`sender_id` and `content`. A missing one raises `msgspec.ValidationError`
naming the field.

---

## 6. API reference

### 6.1 Client

```python
# fragment
from tdpy import TypedClient
from tdpy.core import RetryPolicy

client = TypedClient(
    update_queue_size=1,  # see hard rule 8
    retry=RetryPolicy(retry_server_errors=False),  # optional; retries rate limits only
    poll_timeout=1.0,  # receive-loop block time; bounds shutdown latency
    library_path=None,  # or pass the tdjson path instead of TDJSON_PATH
)
```

| Use | Call |
|---|---|
| Any TDLib function | `await client.<snake_case_name>(**kwargs)` — e.g. `await client.get_chat(chat_id=1)` |
| One of the 28 synchronous functions | `client.sync.<name>(**kwargs)` — no `await` |
| Raw request, plain dicts | `await client.invoke({"@type": "getMe"})` |
| Watch updates without consuming | `unsubscribe = client.subscribe(callback)` |
| Lifecycle | `async with TypedClient() as client:` — `close()` waits for TDLib to flush |

Rules for generated methods and types:

- Arguments are keyword-only. Optional arguments default to `None` and are left
  off the wire. Scalars default to their zero value.
- A parameter typed with an abstract class — `ReplyMarkup`,
  `InputMessageContent`, `ButtonStyle` — takes one concrete variant:
  `types.ReplyMarkupInlineKeyboard(...)`, `types.InputMessageText(...)`,
  `types.ButtonStyleDefault()`.
- Required nested objects must be built. Text is never a bare `str`:
  `types.InputMessageText(text=types.FormattedText(text="hi"))`.
- Results are generated types. A union result narrows with `isinstance`.
- **64-bit fields** (`callback_query_id`, `custom_emoji_id`, `sticker.set_id`)
  are `Int64`, an `int` subclass. Pass received values straight back. Build a
  literal with `Int64(123)`. **Chat, user and message ids are plain `int`.**
- Every function's docstring comes from TDLib's schema:
  `help(TypedClient.send_message)`.

### 6.2 Logging in

```python
# fragment
from tdpy.auth import BotCredentials, EnvCredentials, TdlibSettings, authorize

settings = TdlibSettings.from_env()  # TDPY_API_ID, TDPY_API_HASH, TDPY_DATABASE_DIRECTORY (env or .env)
await authorize(client, settings, BotCredentials("123:ABC"), timeout=60)
await authorize(client, settings, EnvCredentials())  # TDPY_BOT_TOKEN or TDPY_PHONE (env or .env)
```

| `TdlibSettings` field | Default | Notes |
|---|---|---|
| `api_id`, `api_hash` | required | `from_env()` raises `MissingCredentialsError` naming what is missing |
| `database_directory` | `Path("td_session")` | The session. One process per directory. |
| `files_directory` | the database directory | Where downloads land |
| `use_message_database` | `True` | Implies `use_chat_info_database`, which implies `use_file_database` |
| `use_test_dc` | `False` | Telegram's test servers |
| `device_model` | `"tdpy"` | Shown in the account's device list |

`authorize` returns once the session is ready. It raises `SessionClosedError`
if TDLib closes first, `UnsupportedStepError` if it asks for something the
credentials cannot supply, and `TDLibError` if Telegram rejects a step — for
example an invalid token.

### 6.3 Routers and handlers

```python
# fragment
from tdpy.dispatch import Router

router = Router()
admin = Router()
router.include(admin)  # nested: searched after router's own handlers
```

| Decorator | Update it handles | The `event` argument is |
|---|---|---|
| `on_message(*filters)` | `UpdateNewMessage` | `types.Message` |
| `on_edited_message(*filters)` | `UpdateMessageEdited` | the update — only `chat_id`, `message_id`, `edit_date`, `reply_markup` |
| `on_callback_query(*filters)` | `UpdateNewCallbackQuery` | the update |
| `on_inline_query(*filters)` | `UpdateNewInlineQuery` | the update |
| `on_chat_join_request(*filters)` | `UpdateNewChatJoinRequest` | `types.ChatJoinRequest`; the chat id is on `ctx.update.chat_id` |
| `on(types.UpdateMessageSendSucceeded, *filters)` | that update | `types.Message` |
| `on(types.UpdateMessageSendFailed, *filters)` | that update | `types.Message` |
| `on(types.UpdateNewChat, *filters)` | that update | `types.Chat` |
| `on(types.AnyOtherUpdate, *filters)` | that update | the update |
| `on((TypeA, TypeB), *filters)` | either type | as above for each |
| `on_update(*filters)` | every update | the update |

- Several positional filters are combined with *and*.
- A decorator returns the function unchanged, so handlers stay callable in tests.
- Raise `SkipHandler` inside a handler to pass the event to the next match.

### 6.4 Filters

| Filter | Matches | Leaves in `ctx.data` |
|---|---|---|
| `Text(*values, ignore_case=False, startswith=False)` | Message text or caption; no values means "has any text". An empty caption is not text. | — |
| `Command(*names, prefixes="/", ignore_case=True, username=None)` | `/name args`, including `/name@botname`; no names means any command | `"command"`: `CommandInfo(name, prefix, args)` |
| `Regex(pattern, search=True)` | Text or caption | `"match"`: the `re.Match` |
| `ContentType(*kinds)` | `message.content` is one of the classes, or TDLib tags like `"messagePhoto"` | — |
| `FromUser(*user_ids)` | Sent by one of these users. Never matches channel posts, which have no user. | — |
| `InChat(*chat_ids)` | `event.chat_id` is one of these | — |
| `IsOutgoing()` | Sent by this account | — |
| `StateIs(*states)` | The conversation's state is one of these; `None` means no state | — |
| `Never()` | Nothing | — |
| any `(event, ctx) -> bool` callable, sync or async | Whatever it returns | — |

Compose with `&`, `|` and `~`; they short-circuit. The left operand must be a
`Filter`. To combine two plain functions, wrap one: `as_filter(f) & g`.

`Command` without `username` accepts a mention of any bot. In groups, pass
`username="your_bot"` to ignore commands addressed to other bots. By default
Telegram's privacy mode shows a bot in a group only commands and replies to
it; change that with @BotFather's `/setprivacy`.

Helpers for your own filters: `message_text(event) -> str | None` (text or
caption) and `sender_id(event) -> int | None` (the user id, `None` for channel
posts).

### 6.5 Context

| Attribute | Type | Meaning |
|---|---|---|
| `ctx.client` | `Client` | The client the update arrived on. Narrow it to `TypedClient` for generated methods — see `api(ctx)` in the reference bot. |
| `ctx.update` | `types.Update` | The whole update, even when `event` is narrower |
| `ctx.data` | `dict[str, Any]` | Shared by filters, middleware and the handler |
| `ctx.state` | `FSMContext \| None` | Conversation state; `None` without storage |
| `ctx.fsm` | `FSMContext` | Same, but raises `RuntimeError` when the dispatcher has no storage |

### 6.6 Dispatcher

```python
# fragment
from tdpy.dispatch import Dispatcher
from tdpy.fsm import MemoryStorage

dispatcher = Dispatcher(
    router,
    storage=MemoryStorage(),  # enables ctx.fsm
    workers=1,  # >1 handles updates concurrently and loses their order
    queue_size=0,  # 0 = unbounded; a bound drops the oldest updates
    on_error=report,  # plain function (error, event, ctx) -> None
)
async with dispatcher.run(client):
    ...  # dispatching while this block runs; leaving it drains the queue
```

- `dispatcher.handled` counts updates that ran a handler, `dispatcher.dropped`
  updates lost to a bounded queue, `dispatcher.undecodable` update types this
  tdpy build does not know — a newer TDLib sends them, and they are skipped.
- A handler that raises never stops dispatch. The exception goes to `on_error`,
  or is logged by `tdpy.dispatch.dispatcher`.
- With `workers=1`, updates are handled one at a time, in order. Raise it only
  when handlers are slow *and* independent: two updates from one user can then
  race each other through conversation state.

### 6.7 Middleware

```python
# fragment
from typing import Any

from tdpy.dispatch import Context, NextHandler


@router.middleware
async def example(handler: NextHandler, event: Any, ctx: Context) -> Any:
    ...  # before
    result = await handler(event, ctx)  # skip this line to drop the update
    ...  # after
    return result
```

- Middleware runs **after** a handler has matched, wrapped around that handler.
  Not calling `handler` consumes the update: dispatch does not try later handlers.
- Registration order is outermost first. A parent router's middleware wraps its
  children's handlers.

### 6.8 Conversation state

```python
# fragment
from tdpy.fsm import State, StatesGroup


class Form(StatesGroup):
    name = State()  # stored as "Form:name"
    age = State()


await ctx.fsm.set_state(Form.name)
await ctx.fsm.get_state()  # "Form:name" or None
await ctx.fsm.update_data(name="Ada")  # merges and returns the whole dict
await ctx.fsm.get_data()
await ctx.fsm.set_data({})
await ctx.fsm.clear()  # state and data both
```

- State is keyed by client, chat and user: one person in two chats holds two
  conversations; two people in one group hold two conversations.
- `MemoryStorage()` loses everything on restart.
  `RedisStorage.from_url("redis://localhost", ttl=86400)` survives restarts
  (install `tdpy[redis]`). Set a `ttl`, or abandoned conversations leak a key
  per user.
- Stored data must be JSON-serialisable for Redis.

### 6.9 Helpers

| Helper | Signature | Notes |
|---|---|---|
| `send_text` | `(client, chat_id, text, *, parse_mode=None, reply_to=None, disable_notification=False, link_preview_options=None, clear_draft=True)` | No `reply_markup` — use `client.send_message` for keyboards |
| `reply_text` | `(client, message, text, **send_text_kwargs)` | Replies in the message's chat |
| `parse` | `(client, text, parse_mode="markdown")` | Synchronous. `"markdown"` is MarkdownV2; `"html"` also works |
| `text_content` | `(client, text, *, parse_mode=None, ...)` | Builds `InputMessageText` |
| `download` | `(client, file_id, *, priority=1, progress=None, timeout=None)` | Returns the completed `types.File`; `file.local.path` |
| `upload` | `(client, path_or_input_file, *, file_type=None, progress=None, timeout=None)` | Rarely needed — sending a `local_file` uploads it |
| `local_file` | `(path)` | `types.InputFileLocal`; raises `FileNotFoundError` early |
| `iter_history`, `iter_search` | async iterators | Mainly for user accounts; Telegram does not let bots read arbitrary history |

`parse_mode=None` sends text literally. Keep it `None` for text that came from
users. MarkdownV2 rejects unescaped reserved characters such as `.` and `-`
with `TDLibError`.

### 6.10 Errors

| Exception | When |
|---|---|
| `TDLibError` | Base of every error TDLib returns. `.code`, `.message`, `.request` |
| `BadRequest` | 400 and 406 — malformed request, unknown chat, invalid token |
| `Unauthorized` | 401 |
| `Forbidden` | 403 — for example, the user blocked the bot |
| `NotFound` | 404 |
| `FloodWait` | 420/429 or a `FLOOD_WAIT_n` message. `.retry_after` is seconds to wait |
| `InternalError` | 5xx |
| `ClientClosedError`, `ReceiverError` | The client closed, or its receive loop died, while a request was pending |

All import from `tdpy.core`. `TdpyError` from `tdpy` is the base of everything.

`RetryPolicy(max_attempts=3, max_flood_wait=60.0, base_backoff=0.5,
max_backoff=30.0, jitter=0.1, retry_server_errors=True)` applies to every call
on a client built with `retry=`. Retrying a flood wait cannot double-send —
Telegram did not run the request. Retrying a 5xx can, so pass
`retry_server_errors=False` for a client that sends messages.

---

## 7. Recipes

Each recipe is a complete module with its own `router`. Include it in your
root router with `root.include(recipe.router)`.

### 7.1 Commands with arguments

```python
"""Commands with arguments."""

from __future__ import annotations

import random

from tdpy.api import types
from tdpy.dispatch import Context, Router
from tdpy.dispatch.filters import Command, CommandInfo, IsOutgoing
from tdpy.helpers import reply_text

router = Router()


@router.on_message(Command("roll", "dice") & ~IsOutgoing())
async def roll(message: types.Message, ctx: Context) -> None:
    command: CommandInfo = ctx.data["command"]  # left by the Command filter
    sides = max(2, int(command.args)) if command.args.isdigit() else 6
    await reply_text(ctx.client, message, f"{random.randint(1, sides)} (d{sides})")
```

### 7.2 Inline keyboard, callback query and editing the message

```python
"""An inline keyboard whose buttons edit the message they belong to."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tdpy import TypedClient
from tdpy.api import types
from tdpy.dispatch import Context, Router
from tdpy.dispatch.filters import Command, IsOutgoing

router = Router()


def api(ctx: Context) -> TypedClient:
    if not isinstance(ctx.client, TypedClient):
        raise TypeError("start the bot with TypedClient")
    return ctx.client


def callback_button(label: str, data: bytes) -> types.InlineKeyboardButton:
    if len(data) > 64:
        raise ValueError("Telegram caps callback data at 64 bytes")
    return types.InlineKeyboardButton(
        text=label,
        style=types.ButtonStyleDefault(),
        type=types.InlineKeyboardButtonTypeCallback(data=data),
    )


def url_button(label: str, url: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(
        text=label,
        style=types.ButtonStyleDefault(),
        type=types.InlineKeyboardButtonTypeUrl(url=url),
    )


def counter_markup(value: int) -> types.ReplyMarkupInlineKeyboard:
    return types.ReplyMarkupInlineKeyboard(
        rows=[
            [
                callback_button("-1", f"count:{value - 1}".encode()),
                callback_button("+1", f"count:{value + 1}".encode()),
            ],
            [url_button("TDLib docs", "https://core.telegram.org/tdlib")],
        ]
    )


def counter_text(value: int) -> types.InputMessageText:
    return types.InputMessageText(text=types.FormattedText(text=f"Count: {value}"))


def data_prefix(prefix: bytes) -> Callable[[Any, Context], bool]:
    """A filter on callback data. Plain callables work as filters."""

    def check(update: Any, ctx: Context) -> bool:
        payload = getattr(update, "payload", None)
        return isinstance(payload, types.CallbackQueryPayloadData) and payload.data.startswith(
            prefix
        )

    return check


@router.on_message(Command("counter") & ~IsOutgoing())
async def counter(message: types.Message, ctx: Context) -> None:
    await api(ctx).send_message(
        chat_id=message.chat_id,
        reply_markup=counter_markup(0),
        input_message_content=counter_text(0),
    )


@router.on_callback_query(data_prefix(b"count:"))
async def on_count(update: types.UpdateNewCallbackQuery, ctx: Context) -> None:
    payload = update.payload
    if not isinstance(payload, types.CallbackQueryPayloadData):
        return
    value = int(payload.data.removeprefix(b"count:"))
    client = api(ctx)
    await client.edit_message_text(
        chat_id=update.chat_id,
        message_id=update.message_id,
        reply_markup=counter_markup(value),
        input_message_content=counter_text(value),
    )
    await client.answer_callback_query(callback_query_id=update.id)  # always answer
```

`answer_callback_query` also takes `text` (a toast), `show_alert=True` (a
dialog instead of a toast) and `url`.

### 7.3 Reply keyboard

Buttons under the input field. Pressing one sends its text as an ordinary
message, so `Text` matches it.

```python
"""A reply keyboard, and removing it."""

from __future__ import annotations

from tdpy import TypedClient
from tdpy.api import types
from tdpy.dispatch import Context, Router
from tdpy.dispatch.filters import Command, IsOutgoing, Text

router = Router()


def api(ctx: Context) -> TypedClient:
    if not isinstance(ctx.client, TypedClient):
        raise TypeError("start the bot with TypedClient")
    return ctx.client


def text_button(label: str) -> types.KeyboardButton:
    return types.KeyboardButton(
        text=label, style=types.ButtonStyleDefault(), type=types.KeyboardButtonTypeText()
    )


def say(text: str) -> types.InputMessageText:
    return types.InputMessageText(text=types.FormattedText(text=text))


@router.on_message(Command("menu") & ~IsOutgoing())
async def menu(message: types.Message, ctx: Context) -> None:
    keyboard = types.ReplyMarkupShowKeyboard(
        rows=[[text_button("Help"), text_button("Close menu")]],
        resize_keyboard=True,
        one_time=True,
    )
    await api(ctx).send_message(
        chat_id=message.chat_id, reply_markup=keyboard, input_message_content=say("Pick one")
    )


@router.on_message(Text("Close menu") & ~IsOutgoing())
async def close_menu(message: types.Message, ctx: Context) -> None:
    await api(ctx).send_message(
        chat_id=message.chat_id,
        reply_markup=types.ReplyMarkupRemoveKeyboard(),
        input_message_content=say("Menu closed"),
    )
```

### 7.4 A multi-step conversation with validation and cancel

```python
"""A conversation that validates its input and can be cancelled."""

from __future__ import annotations

from tdpy.api import types
from tdpy.dispatch import Context, Router
from tdpy.dispatch.filters import Command, IsOutgoing, StateIs, Text, message_text
from tdpy.fsm import State, StatesGroup
from tdpy.helpers import reply_text

router = Router()
INCOMING = ~IsOutgoing()


class Order(StatesGroup):
    item = State()
    quantity = State()


# /cancel first: "/cancel" is also text, and the state handlers below would
# otherwise take it as an answer.
@router.on_message(Command("cancel") & INCOMING, StateIs(Order.item, Order.quantity))
async def cancel(message: types.Message, ctx: Context) -> None:
    await ctx.fsm.clear()
    await reply_text(ctx.client, message, "Cancelled.")


@router.on_message(Command("order") & INCOMING, StateIs(None))
async def begin(message: types.Message, ctx: Context) -> None:
    await ctx.fsm.set_state(Order.item)
    await reply_text(ctx.client, message, "What would you like? (/cancel to stop)")


@router.on_message(StateIs(Order.item) & Text() & INCOMING)
async def got_item(message: types.Message, ctx: Context) -> None:
    await ctx.fsm.update_data(item=message_text(message))
    await ctx.fsm.set_state(Order.quantity)
    await reply_text(ctx.client, message, "How many?")


@router.on_message(StateIs(Order.quantity) & Text() & INCOMING)
async def got_quantity(message: types.Message, ctx: Context) -> None:
    text = message_text(message) or ""
    if not text.isdigit():
        # Stay in the same state: the next message is another attempt.
        await reply_text(ctx.client, message, "Please send a whole number.")
        return
    data = await ctx.fsm.update_data(quantity=int(text))
    await ctx.fsm.clear()
    await reply_text(ctx.client, message, f"Ordered {data['quantity']} x {data['item']}.")
```

### 7.5 Sending a photo and a document

```python
"""Sending media from local files."""

from __future__ import annotations

from pathlib import Path

from tdpy import TypedClient
from tdpy.api import types
from tdpy.dispatch import Context, Router
from tdpy.dispatch.filters import Command, IsOutgoing
from tdpy.helpers import local_file

router = Router()
PHOTO = Path("assets/cat.jpg")
REPORT = Path("assets/report.pdf")


def api(ctx: Context) -> TypedClient:
    if not isinstance(ctx.client, TypedClient):
        raise TypeError("start the bot with TypedClient")
    return ctx.client


@router.on_message(Command("cat") & ~IsOutgoing())
async def cat(message: types.Message, ctx: Context) -> None:
    client = api(ctx)
    await client.send_chat_action(chat_id=message.chat_id, action=types.ChatActionUploadingPhoto())
    await client.send_message(
        chat_id=message.chat_id,
        input_message_content=types.InputMessagePhoto(
            photo=types.InputPhoto(photo=local_file(PHOTO)),
            caption=types.FormattedText(text="A cat"),
        ),
    )


@router.on_message(Command("report") & ~IsOutgoing())
async def report(message: types.Message, ctx: Context) -> None:
    await api(ctx).send_message(
        chat_id=message.chat_id,
        input_message_content=types.InputMessageDocument(
            document=types.InputDocument(document=local_file(REPORT)),
            caption=types.FormattedText(text="This month's report"),
        ),
    )
```

TDLib uploads a `local_file` as part of sending. To send a file again without
re-uploading, use `types.InputFileRemote(id=file.remote.id)` from a file you
already sent or received.

### 7.6 Receiving and downloading a photo

```python
"""Downloading the largest size of a photo someone sends."""

from __future__ import annotations

from tdpy.api import types
from tdpy.dispatch import Context, Router
from tdpy.dispatch.filters import ContentType, IsOutgoing
from tdpy.helpers import download, reply_text

router = Router()


@router.on_message(ContentType(types.MessagePhoto) & ~IsOutgoing())
async def save_photo(message: types.Message, ctx: Context) -> None:
    content = message.content
    if not isinstance(content, types.MessagePhoto) or not content.photo.sizes:
        return
    largest = max(content.photo.sizes, key=lambda size: size.width * size.height)
    file = await download(ctx.client, largest.photo.id, priority=16, timeout=120)
    await reply_text(ctx.client, message, f"Saved to {file.local.path}")
```

Downloads land in `TdlibSettings.files_directory`. `download` raises
`FileTransferError` if TDLib stops before the file is complete.

### 7.7 Middleware: timing, rate limiting and admin-only commands

```python
"""Middleware around every handler, and a router only admins can reach."""

from __future__ import annotations

import logging
import time
from typing import Any

from tdpy.api import types
from tdpy.dispatch import Context, NextHandler, Router
from tdpy.dispatch.filters import Command, FromUser, IsOutgoing, sender_id
from tdpy.helpers import reply_text

log = logging.getLogger("bot.middleware")
router = Router()
admin = Router()
router.include(admin)  # router's middleware wraps admin's handlers too

ADMIN_IDS = frozenset({123456789})
_last_seen: dict[int, float] = {}


@router.middleware
async def timed(handler: NextHandler, event: Any, ctx: Context) -> Any:
    started = time.monotonic()
    try:
        return await handler(event, ctx)
    finally:
        log.info("%s took %.3fs", type(event).__name__, time.monotonic() - started)


@router.middleware
async def one_per_second(handler: NextHandler, event: Any, ctx: Context) -> Any:
    user = sender_id(event)
    now = time.monotonic()
    if user is not None:
        if now - _last_seen.get(user, 0.0) < 1.0:
            return None  # not calling the handler drops the update
        _last_seen[user] = now
    return await handler(event, ctx)


@admin.on_message(Command("stats") & FromUser(*ADMIN_IDS) & ~IsOutgoing())
async def stats(message: types.Message, ctx: Context) -> None:
    await reply_text(ctx.client, message, "Only admins see this.")
```

A non-admin's `/stats` does not match `stats`, so dispatch moves on to later
handlers — such as an echo. Add a handler for `Command("stats")` without the
`FromUser` filter after it to refuse explicitly.

### 7.8 Rate limits and expected failures

```python
"""Sending to many chats without tripping Telegram's limits."""

from __future__ import annotations

import asyncio

from tdpy import TypedClient
from tdpy.core import FloodWait, RetryPolicy, TDLibError
from tdpy.helpers import send_text


def make_client() -> TypedClient:
    # Retry rate limits only. Telegram did not run a request that hit a flood
    # wait, so repeating it cannot double-send; a 5xx retry could.
    return TypedClient(
        update_queue_size=1,
        retry=RetryPolicy(max_attempts=3, max_flood_wait=30, retry_server_errors=False),
    )


async def broadcast(client: TypedClient, chat_ids: list[int], text: str) -> dict[int, str]:
    """Send to every chat; return the failures by chat id."""
    failures: dict[int, str] = {}
    for chat_id in chat_ids:
        try:
            await client.get_chat(chat_id=chat_id)  # the chat must be known first
            await send_text(client, chat_id, text)
        except FloodWait as wait:
            failures[chat_id] = f"rate limited for {wait.retry_after:.0f}s"
            await asyncio.sleep(wait.retry_after)
        except TDLibError as error:  # blocked the bot, chat gone, ...
            failures[chat_id] = f"{error.code} {error.message}"
        await asyncio.sleep(0.05)  # stay well under the limits
    return failures
```

Telegram's guidance for bots is roughly 30 messages per second overall, and
far less to a single chat. A `FloodWait` escaping the retry policy means the
wait was longer than `max_flood_wait`.

### 7.9 Background work alongside the dispatcher

```python
"""A periodic job that runs for as long as the bot does."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from tdpy import TypedClient
from tdpy.core import TDLibError
from tdpy.dispatch import Dispatcher
from tdpy.helpers import send_text

log = logging.getLogger("bot.jobs")


async def remind(client: TypedClient, chat_id: int, every: float) -> None:
    await client.get_chat(chat_id=chat_id)  # load the chat before sending to it
    while True:
        await asyncio.sleep(every)
        try:
            await send_text(client, chat_id, "Reminder")
        except TDLibError:
            # An exception escaping a task kills it silently; log and go on.
            log.exception("reminder failed")


async def run(client: TypedClient, dispatcher: Dispatcher, chat_id: int, stop: asyncio.Event) -> None:
    async with dispatcher.run(client):
        job = asyncio.create_task(remind(client, chat_id, every=3600))
        try:
            await stop.wait()
        finally:
            job.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await job
```

Sending to a chat the bot has not seen in this session needs
`await client.get_chat(chat_id=...)` first. For bots, `getChat` fetches the
chat from the server; without it the send fails with a `TDLibError`.

### 7.10 Laying out a larger bot

```text
mybot/
  __main__.py        builds settings, client, dispatcher; starts everything
  handlers/
    __init__.py      root = Router(); root.include(...) in priority order
    admin.py         router = Router()
    conversations.py router = Router()
    fallback.py      router = Router()   # echo / unknown command -- include last
  tests/
    test_admin.py    FakeTDJson + Dispatcher.feed, one file per router
```

```python
# fragment -- handlers/__init__.py
from tdpy.dispatch import Router

from . import admin, conversations, fallback

root = Router("mybot")
root.include(admin.router)  # most specific first
root.include(conversations.router)
root.include(fallback.router)  # catch-alls last: the first match wins
```

---

## 8. Coming from the Bot API

| Bot API | tdpy |
|---|---|
| `getUpdates`, webhooks | None. TDLib pushes updates; run a `Dispatcher`. |
| `sendMessage(chat_id, text)` | `await send_text(client, chat_id, text)` |
| `reply_to_message_id` | `await reply_text(client, message, text)` |
| `parse_mode="MarkdownV2"` | `send_text(..., parse_mode="markdown")` |
| `InlineKeyboardMarkup` | `types.ReplyMarkupInlineKeyboard(rows=[[...]])` |
| `InlineKeyboardButton(text, callback_data="x")` | `types.InlineKeyboardButton(text=..., style=types.ButtonStyleDefault(), type=types.InlineKeyboardButtonTypeCallback(data=b"x"))` |
| `ReplyKeyboardMarkup` / `ReplyKeyboardRemove` | `types.ReplyMarkupShowKeyboard` / `types.ReplyMarkupRemoveKeyboard` |
| `callback_query.data` (str) | `update.payload.data` (bytes) on `types.CallbackQueryPayloadData` |
| `answerCallbackQuery` | `await client.answer_callback_query(callback_query_id=update.id)` |
| `editMessageText` | `await client.edit_message_text(chat_id=..., message_id=..., input_message_content=...)` |
| `deleteMessage` | `await client.delete_messages(chat_id=..., message_ids=[...], revoke=True)` |
| `setMyCommands` | `await client.set_commands(commands=[types.BotCommand(command=..., description=...)])` |
| `sendChatAction("typing")` | `await client.send_chat_action(chat_id=..., action=types.ChatActionTyping())` |
| `sendPhoto(photo=path)` | `send_message(input_message_content=types.InputMessagePhoto(photo=types.InputPhoto(photo=local_file(path))))` |
| `getFile` + download URL | `await download(client, file_id)` |
| `message.text` | `message_text(message)`, or narrow `message.content` to `types.MessageText` and read `.text.text` |
| `message.from_user.id` | `sender_id(message)` |
| `update.message` | The `event` of an `on_message` handler |
| `update.callback_query` | The `event` of an `on_callback_query` handler |

**Message ids differ.** TDLib's id for a server message is the Bot API id
multiplied by 2^20 (1048576). Do not mix the two. Chat and user ids are the
same in both.

---

## 9. Symptoms and fixes

| Symptom | Cause | Fix |
|---|---|---|
| The bot replies to its own messages without end | Outgoing messages arrive as `updateNewMessage` | Add `~IsOutgoing()` to every replying message handler |
| `TypeError: Missing required argument 'style'` | `InlineKeyboardButton` / `KeyboardButton` require `style` | `style=types.ButtonStyleDefault()` |
| `TypeError: handler 'x' must take (event) or (event, ctx)` on import | Wrong number of handler parameters | Two parameters: `(event, ctx)` |
| A handler never runs | An earlier handler matched first, or its filter never matches | Register specific handlers earlier; test the filter alone |
| Callback buttons keep spinning | The callback query was never answered | `await client.answer_callback_query(callback_query_id=update.id)` |
| `payload.data == "x"` is always false | Callback data is `bytes` | Compare with `b"x"` |
| Memory grows steadily for days | `client.updates()` buffer is unbounded and nothing reads it | `TypedClient(update_queue_size=1)` |
| Messages sent while the bot was down are never handled | Dispatcher started after `authorize` | Enter `dispatcher.run(client)` before `authorize` |
| `BadRequest: [400] Can't lock file ".../td.binlog"` | Two processes share a database directory | Give each process its own `database_directory` |
| `"Client" has no attribute "send_message"` from mypy | `ctx.client` is annotated as `Client` | Narrow it: `api(ctx)` from the reference bot |
| A test passes although the handler crashed | `Dispatcher.feed` reports exceptions to `on_error` | Collect errors in `on_error` and assert the list is empty |
| The error handler never runs, or warns "coroutine was never awaited" | `on_error` is `async def` | Make it a plain function; start async work with `asyncio.create_task` |
| `RuntimeError: no FSM storage configured` | `ctx.fsm` without storage | `Dispatcher(router, storage=MemoryStorage())` |
| Conversation state is gone after a restart | `MemoryStorage` lives in memory | `RedisStorage` |
| Sending to a known chat id raises `TDLibError` | This session has not loaded the chat | `await client.get_chat(chat_id=...)` first |
| `FloodWait` | Telegram rate limit | Wait `retry_after`; `RetryPolicy(retry_server_errors=False)`; pace sends |
| Commands are ignored in a group | Privacy mode, or another bot's `@mention` | @BotFather `/setprivacy`; `Command(..., username="your_bot")` |
| The edit handler has no text | `updateMessageEdited` carries ids only | `await client.get_message(chat_id=..., message_id=...)` |
| `object ... can't be used in 'await' expression` | Awaiting a `client.sync` method | Call `client.sync.*` without `await` |
| A coroutine was never awaited | A generated method called without `await` | `await` every `client.*` method |
| The whole bot stops responding for a while | A blocking call inside a handler | Async I/O, or `await asyncio.to_thread(func)` |
| `TDLibError` with `Character '.' is reserved` | Unescaped MarkdownV2 | Escape it, or `parse_mode=None` |
| `TDJsonNotFoundError` | Library missing, or a dependency of it missing | Set `TDJSON_PATH` in `.env`; read the OS error it lists |
| `MissingCredentialsError` | `TDPY_API_ID` / `TDPY_API_HASH` unset | Add them to `.env`, and run from the directory that holds it |
| `msgspec.ValidationError: Object missing required field` in a test | A scripted fake reply lacks a required field | Add the field it names |

---

## 10. User accounts instead of bots

The same framework logs in as a person with a phone number. Everything above
applies, with these differences:

```python
# fragment
from tdpy.auth import ConsoleCredentials

await authorize(client, settings, ConsoleCredentials(phone="+15551234567"))
```

- The first login needs a code Telegram sends to the account, and possibly the
  two-step verification password. `ConsoleCredentials` prompts for both.
  Afterwards the database directory keeps the session.
- Without a terminal, use `CallbackCredentials(login_method=...,
  authentication_code=..., password=...)` with your own async functions. TDLib
  stores a half-finished login, so a process can exit at the code prompt and a
  later one can resume there.
- Methods marked "for bots only" in TDLib's docs — `answer_callback_query`,
  `set_commands`, `edit_message_reply_markup` — fail for user accounts, and
  user-only methods fail for bots, with a `TDLibError`.
- `~IsOutgoing()` matters even more: a user account sees everything it sends
  from every device.
- Automating a personal account is subject to Telegram's terms of service.
  Aggressive automation risks limits on the account.

---

## 11. Before you say you are done

- [ ] Every message handler that sends something is gated with `~IsOutgoing()`.
- [ ] Handlers are registered specific to general; `/cancel` and state
      handlers come before catch-all text handlers.
- [ ] Every `InlineKeyboardButton` and `KeyboardButton` has a `style`.
- [ ] Every callback query handler calls `answer_callback_query`.
- [ ] The client is `TypedClient(update_queue_size=1)`.
- [ ] `dispatcher.run(client)` is entered before `authorize`.
- [ ] `on_error` is a plain function.
- [ ] Credentials come from `.env` or the environment; nothing secret is in the code, and `.env` is not committed.
- [ ] Each process has its own `database_directory`.
- [ ] No blocking calls in handlers.
- [ ] Offline tests exist, collect handler errors, assert none happened, and pass.
- [ ] `mypy --strict` passes, with `ctx.client` narrowed where generated methods are used.
