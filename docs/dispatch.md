# Routing updates

```python
from tdpy.dispatch import Dispatcher, Router
from tdpy.dispatch.filters import Command, IsOutgoing

router = Router()


@router.on_message(Command("start") & ~IsOutgoing())
async def start(message, ctx):
    ...


async with Dispatcher(router).run(client):
    await asyncio.Event().wait()
```

## Handlers

A handler takes `(event, ctx)`, or just `(event)` if it does not need the
context. Which shape you wrote is decided once at registration; a signature
that is neither raises `TypeError` there and then, rather than on the first
matching update.

`event` is the *subject* of the update — a `Message` for `on_message`. An
`on_update()` handler gets the whole update, because no single subject exists
for every type.

`ctx` carries:

| | |
|---|---|
| `ctx.client` | the client it arrived on, for replying |
| `ctx.update` | the full typed update, even when `event` is narrower |
| `ctx.data` | scratch space shared with middleware and filters |
| `ctx.state` | conversation state, when the dispatcher has storage |

## Filters

Filters compose with `&`, `|` and `~`, and short-circuit.

```python
from tdpy.dispatch.filters import Command, ContentType, FromUser, InChat, Regex, Text

Text("hi", ignore_case=True)
Command("ban", prefixes="/!")
Regex(r"order #(\d+)")
FromUser(1, 2) | InChat(-100123)
ContentType(types.MessagePhoto)
```

Some leave their findings in `ctx.data` so the handler need not redo the work:

```python
@router.on_message(Regex(r"order #(\d+)"))
async def show_order(message, ctx):
    number = ctx.data["match"].group(1)


@router.on_message(Command("ban"))
async def ban(message, ctx):
    target = ctx.data["command"].args
```

A plain function works too:

```python
@router.on_message(Text() & (lambda event, ctx: event.chat_id > 0))
async def in_private(message, ctx):
    ...
```

Every built-in filter is pure — no requests — because a filter may be evaluated
against every update that arrives.

### Things worth knowing

`Command` strips Telegram's `@botname` suffix, so a `/start` handler still
fires on `/start@yourbot` in a group. Pass `username=` to reject *other* bots'
mentions.

`Text` counts captions, so a photo with a caption matches. An empty caption
does not: TDLib's caption field is not nullable, and "no caption" arrives as an
empty string.

`FromUser` does not match channel posts. They carry a `messageSenderChat` and
have no user at all, so reading their chat id as a user id would be wrong.

## Routers nest

```python
root = Router()
root.include(admin.router)
root.include(payments.router)
```

Handlers are tried in registration order, depth-first, and **the first match
wins**. To decline and pass the event on:

```python
from tdpy.dispatch import SkipHandler


@router.on_message()
async def maybe(message, ctx):
    if not interesting(message):
        raise SkipHandler
```

Running every match instead would mean that adding a broad handler silently
changes what the narrow ones do.

## Middleware

```python
@router.middleware
async def timed(handler, event, ctx):
    started = time.monotonic()
    try:
        return await handler(event, ctx)
    finally:
        log.info("%s took %.3fs", type(event).__name__, time.monotonic() - started)
```

Registration order is outermost first. A parent router's middleware wraps its
children's. Middleware can decline to call the handler at all, which is how
rate limits and access checks are written.

## Ordering and failure

Updates are handled **in order** by default — one worker. Telegram's ordering
carries meaning: an edit follows its message, a deletion follows both, and
concurrency discards that. Raise `workers` when handlers are slow and
independent.

```python
Dispatcher(router, workers=8, queue_size=10_000, on_error=report)
```

A handler that raises cannot stop dispatch. The exception goes to `on_error`,
or is logged, and the next update proceeds. An update type this build has never
heard of — a newer TDLib will send them — is counted in
`dispatcher.undecodable` and skipped.

## Conversation state

```python
from tdpy.fsm import MemoryStorage, State, StatesGroup
from tdpy.dispatch.filters import StateIs


class Form(StatesGroup):
    name = State()
    age = State()


@router.on_message(Command("register"), StateIs(None))
async def begin(message, ctx):
    await ctx.fsm.set_state(Form.name)


@router.on_message(StateIs(Form.name))
async def got_name(message, ctx):
    await ctx.fsm.update_data(name=message.content.text.text)
    await ctx.fsm.set_state(Form.age)


dispatcher = Dispatcher(router, storage=MemoryStorage())
```

`StateIs(None)` matches a conversation that has not started — that is how a
handler says "only before the conversation begins".

State is keyed by *(client, chat, user)*, because the same person can be
mid-conversation in several chats, and several people can be mid-conversation
in one group.

`MemoryStorage` loses everything on restart. For state that must survive a
deploy:

```python
from redis.asyncio import Redis
from tdpy.fsm import RedisStorage

storage = RedisStorage(Redis.from_url("redis://localhost"), ttl=86400)
```

Set a `ttl` unless you clear state yourself; an abandoned conversation
otherwise leaks a key per user forever.
