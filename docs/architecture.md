# How tdpy is put together

```
dispatch/ + fsm/   routers · filters · middleware · conversation state
helpers/           files · parse modes · paging · common sends
api/               2183 types · 1022 functions · 1022 methods   [GENERATED]
core/              Client · @extra correlation · errors · retry
_ffi/              loader · ctypes ABI · the one receive thread
```

Each layer uses only the ones below it. `core` never imports `api`, which is
why `TdlibSettings` lives in `auth/` rather than `core/` — it builds a generated
request object.

## `_ffi`: the only code that touches ctypes

Everything above is written against the `TDJsonABI` protocol, never the
concrete binding. That is what lets the whole framework be tested against
`FakeTDJson`, an in-process double, with no compiled TDLib anywhere.

Two invariants live here and nowhere else:

**The pointer TDLib returns is freed on the next call.** Declaring `restype`
as `ctypes.c_char_p` makes ctypes copy the string into a fresh `bytes` before
returning, so nothing borrowed escapes the module. There is a test asserting
the binding, so replacing it with `c_void_p` fails loudly rather than corrupting
memory.

**`td_receive` must never run on two threads at once.** The C API does not
check; it corrupts. A non-blocking lock turns that into `ConcurrentReceiveError`.

## `core`: two threads meeting

TDLib's receive queue is per-*process*, not per-client: one `td_receive` loop
returns responses and updates for every client id, tagged with `@client_id`. So
one thread owns it, reference-counted by subscriber — it starts with the first
client and stops with the last.

That thread cannot run coroutines, so everything it reads is bounced onto the
event loop with `call_soon_threadsafe`. As a result the pending-request table is
touched only from the loop thread and needs no lock.

Requests and responses are matched by TDLib's `@extra`, which it echoes back
verbatim. Updates never carry one — that is exactly what separates the two
streams. A response whose request already timed out is dropped rather than
delivered as an update, since it is not one.

**If the receive loop dies, every pending request fails** with `ReceiverError`.
The alternative is an `await` that never returns, which is the worst failure
mode a network client can have.

Two ways to see updates:

- `client.updates()` consumes the stream.
- `client.subscribe(fn)` observes it without consuming.

The authorization flow and the file helpers use `subscribe`, so an application
reading `updates()` across start-up or a download loses nothing.

## `api`: generated, and checked against its schema

`types.py`, `functions.py` and `methods.py` come from `tools/td_api.tl` via
`python -m tdpy.codegen`. Each records the schema's sha256, and
`--check` fails CI when they disagree — a schema bump cannot ship half-applied.

Some decisions that shaped the output:

**Cycles are unavoidable.** `Message` holds a `MessageContent` which holds a
`Message`. Generated modules rely on `from __future__ import annotations` and
let msgspec resolve the strings when a decoder is first built.

**`Int64` is a real `int` subclass.** TDLib sends 64-bit values as JSON
*strings*, because JSON numbers are doubles and lose precision past 2^53. Chat,
user and message ids are `int53` and arrive as plain ints; `custom_emoji_id` and
`sticker.set_id` are the `int64` cases.

**A field can shadow a builtin.** `chatActiveStories` really does have a field
named `list`, which shadows the builtin for the rest of the class body and
makes `list[StoryInfo]` unresolvable at runtime. The generator qualifies those
as `builtins.list`.

**Optionality is only in the prose.** Types say "may be null"; functions say
"pass null if none". Both mean optional, and TDLib never applies either to a
primitive — so nullable fields default to `None`, scalars and vectors to their
zero, and everything else stays required.

**Unknown fields are ignored on decode.** A newer TDLib adding a field must not
break a client built against an older schema. Missing fields still raise, which
is the loud failure you want.

## `dispatch` and `fsm`

Built on `subscribe`. The receive thread enqueues; worker tasks drain. One
worker by default, so updates stay ordered — Telegram's ordering carries
meaning.

First match wins, `SkipHandler` opts out, middleware wraps outermost-first, and
a handler that raises cannot take the loop down with it.

## Testing without TDLib

`FakeTDJson` implements the same five-call ABI in process: scripted responders
answer requests, `push` injects updates, and `receive` blocks the way the real
one does. Around 400 tests run against it on every supported Python and OS.

The handful that need a real library are marked `requires_binary` and skip
themselves; CI runs them in an opt-in job that builds TDLib from source.
