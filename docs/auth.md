# Logging in

TDLib does not offer login as a call you make. It announces what it wants
through `updateAuthorizationState` and waits. `authorize` is the loop that
answers.

```python
from tdpy.auth import ConsoleCredentials, TdlibSettings, authorize

await authorize(client, TdlibSettings.from_env(), ConsoleCredentials())
```

It returns when the session reaches `authorizationStateReady`, and raises if
TDLib asks for something the credentials cannot answer.

## Settings

`TdlibSettings` becomes the `setTdlibParameters` request.

```python
from pathlib import Path
from tdpy.auth import TdlibSettings

settings = TdlibSettings(
    api_id=1234567,
    api_hash="0123456789abcdef0123456789abcdef",
    database_directory=Path("td_session"),
    use_message_database=True,
)
```

`from_env()` reads `TDPY_API_ID`, `TDPY_API_HASH` and optionally
`TDPY_DATABASE_DIRECTORY`, and raises `MissingCredentialsError` naming what is
absent rather than inventing a default.

`use_message_database` implies `use_chat_info_database`, which implies
`use_file_database`. TDLib turns them on regardless; `normalized()` makes that
explicit so the object does not misreport the session.

## Where the answers come from

| Provider | For |
|---|---|
| `ConsoleCredentials()` | A person at a terminal |
| `BotCredentials(token)` | Bots — a token is the whole login |
| `EnvCredentials()` | Unattended runs |
| `CallbackCredentials(...)` | GUIs, web apps, tests |

Console prompts run in a worker thread. `input()` blocks, and blocking the
event loop would stall the very updates that confirm the code was accepted.

```python
from tdpy.auth import CallbackCredentials, PhoneNumber

credentials = CallbackCredentials(
    login_method=lambda: fetch_phone_number(),
    authentication_code=lambda info: prompt_user(info),
    password=lambda state: prompt_password(state.password_hint),
)
```

Only `login_method` is required. Every other step falls back to
`UnsupportedStepError` naming the step — so a bot that somehow reaches a
password prompt fails with *"BotCredentials cannot supply a 2FA password"*
instead of a `None` surfacing three frames later.

## How to log in

`login_method` returns one of three things:

```python
from tdpy.auth import BotToken, PhoneNumber, QrCode

PhoneNumber("+15551234")    # a user account
BotToken("123:ABC")         # a bot
QrCode()                    # scan from an already-authorized device
```

For QR, implement `show_qr_link`. TDLib sends nothing back at that step and
moves on by itself once the code is scanned — either to ready, or to a password
prompt for a 2FA account.

## The states it handles

All thirteen. `WaitTdlibParameters`, `WaitPhoneNumber`, `WaitCode`,
`WaitEmailAddress`, `WaitEmailCode`, `WaitPassword`, `WaitRegistration`,
`WaitOtherDeviceConfirmation`, `Ready`, and the terminal `LoggingOut`,
`Closing`, `Closed`.

Two cannot be satisfied from a library and say so plainly:

- `WaitPremiumPurchase` needs an app-store purchase.
- The terminal states raise `SessionClosedError` — usually a stale database, or
  the account was logged out elsewhere.

## It does not steal your updates

The flow watches the stream through `Client.subscribe` rather than consuming
`Client.updates()`. An application already reading updates across start-up
loses nothing, authorization updates included.

```python
async with TypedClient() as client:
    reader = asyncio.create_task(read_updates(client))   # started first
    await authorize(client, settings, ConsoleCredentials())
    # reader saw every update, including the authorization ones
```

## Sessions

The database in `database_directory` *is* the session. Keep it and start-up is
silent; delete it and the next run logs in from scratch.

Set `database_encryption_key` to encrypt it. A wrong key fails authorization
with a 401, not a decryption error.
