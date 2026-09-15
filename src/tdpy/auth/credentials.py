"""Where the authorization flow gets phone numbers, codes and passwords.

The flow knows the state machine; it does not know whether the answers come
from a terminal, an environment variable, a web form or a queue. That split is
what lets the same flow serve a CLI, a bot and a GUI.
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from tdpy.api import types
from tdpy.exceptions import TdpyError

__all__ = [
    "BotCredentials",
    "BotToken",
    "CallbackCredentials",
    "ConsoleCredentials",
    "Credentials",
    "EnvCredentials",
    "LoginMethod",
    "PhoneNumber",
    "QrCode",
    "Registration",
    "UnsupportedStepError",
]


class UnsupportedStepError(TdpyError):
    """TDLib asked for something this credential provider cannot answer."""


@dataclass(frozen=True, slots=True)
class PhoneNumber:
    """Log in as a user, by phone number."""

    number: str
    settings: types.PhoneNumberAuthenticationSettings | None = None


@dataclass(frozen=True, slots=True)
class BotToken:
    """Log in as a bot, with a token from @BotFather."""

    token: str


@dataclass(frozen=True, slots=True)
class QrCode:
    """Log in by showing a QR code to an already-authorized device."""

    other_user_ids: Sequence[int] = ()


#: What to do when TDLib reaches ``authorizationStateWaitPhoneNumber``.
LoginMethod = PhoneNumber | BotToken | QrCode


@dataclass(frozen=True, slots=True)
class Registration:
    """Details for an account that does not exist yet."""

    first_name: str
    last_name: str = ""
    disable_notification: bool = False


class Credentials(ABC):
    """Supplies each answer the authorization flow needs.

    Only :meth:`login_method` is required. The rest raise
    :class:`UnsupportedStepError` by default, so a bot-token provider does not
    have to implement password recovery it will never be asked for -- and if
    TDLib does ask unexpectedly, the failure names the step instead of
    surfacing as ``None``.
    """

    @abstractmethod
    async def login_method(self) -> LoginMethod:
        """How to authorize: phone number, bot token or QR code."""

    async def authentication_code(self, info: types.AuthenticationCodeInfo) -> str:
        """The login code TDLib just sent. ``info`` says where it went."""
        raise UnsupportedStepError(f"{type(self).__name__} cannot supply a login code")

    async def password(self, state: types.AuthorizationStateWaitPassword) -> str:
        """The two-step verification password. ``state`` carries the hint."""
        raise UnsupportedStepError(f"{type(self).__name__} cannot supply a 2FA password")

    async def email_address(self) -> str:
        """An email address to attach to the account."""
        raise UnsupportedStepError(f"{type(self).__name__} cannot supply an email address")

    async def email_code(self, state: types.AuthorizationStateWaitEmailCode) -> str:
        """The code sent to that email address."""
        raise UnsupportedStepError(f"{type(self).__name__} cannot supply an email code")

    async def registration(self, terms: types.TermsOfService) -> Registration:
        """Details for a brand-new account, once the terms are accepted."""
        raise UnsupportedStepError(f"{type(self).__name__} cannot register a new account")

    async def show_qr_link(self, link: str) -> None:
        """Present ``link`` to the user, who scans it on an authorized device."""
        raise UnsupportedStepError(f"{type(self).__name__} cannot display a QR link")


class BotCredentials(Credentials):
    """The whole of a bot login: one token from @BotFather.

    Bots never see a code, a password or a registration form, so the inherited
    defaults are exactly right -- if TDLib asks for one, something is wrong and
    the resulting error says which step it was.
    """

    def __init__(self, token: str) -> None:
        self.token = token

    async def login_method(self) -> LoginMethod:
        return BotToken(self.token)


class ConsoleCredentials(Credentials):
    """Prompts on the terminal, off the event loop.

    Every prompt runs in a worker thread: ``input`` blocks, and blocking the
    loop would stop the receive thread's work from ever being processed --
    including the very update that says the code was accepted.

    Args:
        phone: Skip the phone prompt by supplying the number.
        bot_token: Log in as a bot instead, without prompting at all.
    """

    def __init__(self, *, phone: str | None = None, bot_token: str | None = None) -> None:
        self.phone = phone
        self.bot_token = bot_token

    @staticmethod
    async def _ask(prompt: str) -> str:
        return (await asyncio.to_thread(input, prompt)).strip()

    @staticmethod
    async def _ask_secret(prompt: str) -> str:
        import getpass

        return (await asyncio.to_thread(getpass.getpass, prompt)).strip()

    async def login_method(self) -> LoginMethod:
        if self.bot_token:
            return BotToken(self.bot_token)
        if self.phone:
            return PhoneNumber(self.phone)
        return PhoneNumber(await self._ask("Phone number (international format): "))

    async def authentication_code(self, info: types.AuthenticationCodeInfo) -> str:
        destination = _describe_code_destination(info)
        return await self._ask(f"Login code{destination}: ")

    async def password(self, state: types.AuthorizationStateWaitPassword) -> str:
        hint = f" (hint: {state.password_hint})" if state.password_hint else ""
        return await self._ask_secret(f"Two-step verification password{hint}: ")

    async def email_address(self) -> str:
        return await self._ask("Email address: ")

    async def email_code(self, state: types.AuthorizationStateWaitEmailCode) -> str:
        pattern = getattr(state.code_info, "email_address_pattern", "")
        suffix = f" sent to {pattern}" if pattern else ""
        return await self._ask(f"Email code{suffix}: ")

    async def registration(self, terms: types.TermsOfService) -> Registration:
        if terms.text.text:
            print(terms.text.text)
        first = await self._ask("First name: ")
        last = await self._ask("Last name (optional): ")
        return Registration(first_name=first, last_name=last)

    async def show_qr_link(self, link: str) -> None:
        print(f"Scan this from an authorized device: {link}")


class EnvCredentials(Credentials):
    """Reads answers from the environment, for unattended logins.

    Codes cannot realistically come from a variable, so only the values that
    can are supported: ``TDPY_BOT_TOKEN``, ``TDPY_PHONE`` and
    ``TDPY_PASSWORD``. Pass ``fallback`` to hand the interactive steps to
    another provider.
    """

    def __init__(self, fallback: Credentials | None = None) -> None:
        self.fallback = fallback

    async def login_method(self) -> LoginMethod:
        token = os.environ.get("TDPY_BOT_TOKEN")
        if token:
            return BotToken(token)
        phone = os.environ.get("TDPY_PHONE")
        if phone:
            return PhoneNumber(phone)
        if self.fallback is not None:
            return await self.fallback.login_method()
        raise UnsupportedStepError("set TDPY_BOT_TOKEN or TDPY_PHONE, or pass a fallback")

    async def authentication_code(self, info: types.AuthenticationCodeInfo) -> str:
        if self.fallback is not None:
            return await self.fallback.authentication_code(info)
        return await super().authentication_code(info)

    async def password(self, state: types.AuthorizationStateWaitPassword) -> str:
        password = os.environ.get("TDPY_PASSWORD")
        if password:
            return password
        if self.fallback is not None:
            return await self.fallback.password(state)
        return await super().password(state)


class CallbackCredentials(Credentials):
    """Builds a provider from async callables, for GUIs, bots and tests.

    Any step left as ``None`` keeps the base class's descriptive failure.
    """

    def __init__(
        self,
        *,
        login_method: Callable[[], Awaitable[LoginMethod]],
        authentication_code: Callable[[types.AuthenticationCodeInfo], Awaitable[str]] | None = None,
        password: Callable[[types.AuthorizationStateWaitPassword], Awaitable[str]] | None = None,
        email_address: Callable[[], Awaitable[str]] | None = None,
        email_code: Callable[[types.AuthorizationStateWaitEmailCode], Awaitable[str]] | None = None,
        registration: Callable[[types.TermsOfService], Awaitable[Registration]] | None = None,
        show_qr_link: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._login_method = login_method
        self._authentication_code = authentication_code
        self._password = password
        self._email_address = email_address
        self._email_code = email_code
        self._registration = registration
        self._show_qr_link = show_qr_link

    async def login_method(self) -> LoginMethod:
        return await self._login_method()

    async def authentication_code(self, info: types.AuthenticationCodeInfo) -> str:
        if self._authentication_code is None:
            return await super().authentication_code(info)
        return await self._authentication_code(info)

    async def password(self, state: types.AuthorizationStateWaitPassword) -> str:
        if self._password is None:
            return await super().password(state)
        return await self._password(state)

    async def email_address(self) -> str:
        if self._email_address is None:
            return await super().email_address()
        return await self._email_address()

    async def email_code(self, state: types.AuthorizationStateWaitEmailCode) -> str:
        if self._email_code is None:
            return await super().email_code(state)
        return await self._email_code(state)

    async def registration(self, terms: types.TermsOfService) -> Registration:
        if self._registration is None:
            return await super().registration(terms)
        return await self._registration(terms)

    async def show_qr_link(self, link: str) -> None:
        if self._show_qr_link is None:
            await super().show_qr_link(link)
            return
        await self._show_qr_link(link)


def _describe_code_destination(info: types.AuthenticationCodeInfo) -> str:
    """Say where the code went, so the user knows which app to open."""
    kind = getattr(info.type, "type_name", "")
    where = {
        "authenticationCodeTypeTelegramMessage": " (sent in Telegram)",
        "authenticationCodeTypeSms": f" (SMS to {info.phone_number})",
        "authenticationCodeTypeCall": " (by phone call)",
        "authenticationCodeTypeFlashCall": " (flash call)",
        "authenticationCodeTypeMissedCall": " (missed call)",
        "authenticationCodeTypeFragment": " (via Fragment)",
    }
    return where.get(kind, "")
