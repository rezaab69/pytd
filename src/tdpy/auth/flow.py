"""Drive TDLib from start-up to an authorized session.

TDLib does not expose login as a call you make; it announces what it wants via
``updateAuthorizationState`` and waits. This module is the loop that answers.

It watches the update stream through :meth:`~tdpy.core.client.Client.subscribe`
rather than consuming :meth:`~tdpy.core.client.Client.updates`, so an
application that starts reading updates before authorizing does not lose the
ones that arrive while it runs -- including the authorization updates
themselves, which some applications display.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from tdpy.api import functions, types
from tdpy.api.base import convert, to_builtins
from tdpy.auth.credentials import BotToken, Credentials, PhoneNumber, QrCode
from tdpy.auth.settings import TdlibSettings
from tdpy.exceptions import TdpyError

if TYPE_CHECKING:
    from tdpy.core.client import Client

__all__ = ["Authorization", "AuthorizationError", "SessionClosedError", "authorize"]

logger = logging.getLogger(__name__)

_UPDATE = "updateAuthorizationState"


class AuthorizationError(TdpyError):
    """Authorization could not be completed."""


class SessionClosedError(AuthorizationError):
    """TDLib logged out or closed while the flow was still running."""


class Authorization:
    """The ``authorizationState`` machine, driven to ``Ready``.

    Args:
        client: A started client. The flow only sends requests and watches
            updates, so any :class:`~tdpy.core.client.Client` will do.
        settings: Answers ``authorizationStateWaitTdlibParameters``.
        credentials: Answers everything after that.

    Example:
        >>> async with TypedClient() as client:                 # doctest: +SKIP
        ...     await Authorization(client, settings, ConsoleCredentials()).run()
    """

    def __init__(
        self,
        client: Client,
        settings: TdlibSettings,
        credentials: Credentials,
    ) -> None:
        self.client = client
        self.settings = settings
        self.credentials = credentials
        self._states: asyncio.Queue[types.AuthorizationState] = asyncio.Queue()
        self._seen: list[str] = []

    @property
    def visited(self) -> tuple[str, ...]:
        """Every authorization state seen, in order. Useful in tests and logs."""
        return tuple(self._seen)

    async def run(self, *, timeout: float | None = None) -> types.AuthorizationStateReady:
        """Answer TDLib until the session is authorized.

        Args:
            timeout: Give up after this many seconds. ``None`` waits as long as
                the user needs, which is usually right -- somebody has to read
                a code off their phone.

        Returns:
            The ``authorizationStateReady`` that ended the flow.

        Raises:
            AuthorizationError: if TDLib asked for something that cannot be
                answered, or the session closed first.
            TDLibError: if TDLib rejected one of the answers.
        """
        unsubscribe = self.client.subscribe(self._observe)
        try:
            # TDLib announces the current state on connect, but asking costs
            # nothing and removes the race for a client that was already up.
            self.client.send_nowait(to_builtins(functions.GetAuthorizationState()))
            if timeout is None:
                return await self._drive()
            return await asyncio.wait_for(self._drive(), timeout)
        finally:
            unsubscribe()

    def _observe(self, payload: dict[str, Any]) -> None:
        """Runs on the event loop thread for every update."""
        if payload.get("@type") != _UPDATE:
            return
        state: types.AuthorizationState = convert(
            payload["authorization_state"], types.AuthorizationState
        )
        self._states.put_nowait(state)

    async def _drive(self) -> types.AuthorizationStateReady:
        while True:
            state = await self._states.get()
            self._seen.append(state.type_name)
            logger.debug("authorization state: %s", state.type_name)
            if isinstance(state, types.AuthorizationStateReady):
                return state
            await self._handle(state)

    async def _handle(self, state: types.AuthorizationState) -> None:
        if isinstance(state, types.AuthorizationStateWaitTdlibParameters):
            await self._invoke(self.settings.to_request())

        elif isinstance(state, types.AuthorizationStateWaitPhoneNumber):
            await self._start_login()

        elif isinstance(state, types.AuthorizationStateWaitCode):
            code = await self.credentials.authentication_code(state.code_info)
            await self._invoke(functions.CheckAuthenticationCode(code=code))

        elif isinstance(state, types.AuthorizationStateWaitEmailAddress):
            address = await self.credentials.email_address()
            await self._invoke(functions.SetAuthenticationEmailAddress(email_address=address))

        elif isinstance(state, types.AuthorizationStateWaitEmailCode):
            code = await self.credentials.email_code(state)
            await self._invoke(
                functions.CheckAuthenticationEmailCode(
                    code=types.EmailAddressAuthenticationCode(code=code)
                )
            )

        elif isinstance(state, types.AuthorizationStateWaitPassword):
            password = await self.credentials.password(state)
            await self._invoke(functions.CheckAuthenticationPassword(password=password))

        elif isinstance(state, types.AuthorizationStateWaitRegistration):
            details = await self.credentials.registration(state.terms_of_service)
            await self._invoke(
                functions.RegisterUser(
                    first_name=details.first_name,
                    last_name=details.last_name,
                    disable_notification=details.disable_notification,
                )
            )

        elif isinstance(state, types.AuthorizationStateWaitOtherDeviceConfirmation):
            # Nothing to send: the user scans the link and TDLib moves on by
            # itself, either to Ready or to WaitPassword for a 2FA account.
            await self.credentials.show_qr_link(state.link)

        elif isinstance(
            state,
            types.AuthorizationStateClosing
            | types.AuthorizationStateClosed
            | types.AuthorizationStateLoggingOut,
        ):
            raise SessionClosedError(
                f"session reached {state.type_name} before authorizing; "
                "the database may be stale or the account was logged out elsewhere"
            )

        elif isinstance(state, types.AuthorizationStateWaitPremiumPurchase):
            raise AuthorizationError(
                "this account requires a Telegram Premium purchase through an app store, "
                "which cannot be completed from here"
            )

        else:  # pragma: no cover - a state added by a newer TDLib
            raise AuthorizationError(f"unhandled authorization state {state.type_name}")

    async def _start_login(self) -> None:
        method = await self.credentials.login_method()
        if isinstance(method, BotToken):
            await self._invoke(functions.CheckAuthenticationBotToken(token=method.token))
        elif isinstance(method, PhoneNumber):
            await self._invoke(
                functions.SetAuthenticationPhoneNumber(
                    phone_number=method.number, settings=method.settings
                )
            )
        elif isinstance(method, QrCode):
            await self._invoke(
                functions.RequestQrCodeAuthentication(other_user_ids=list(method.other_user_ids))
            )
        else:  # pragma: no cover - guards a caller returning something else
            raise AuthorizationError(f"unknown login method {method!r}")

    async def _invoke(self, request: Any) -> None:
        """Send one step's answer, letting TDLib's own error surface as-is."""
        await self.client.invoke(to_builtins(request))


async def authorize(
    client: Client,
    settings: TdlibSettings,
    credentials: Credentials,
    *,
    timeout: float | None = None,
) -> types.AuthorizationStateReady:
    """Run the authorization flow to completion.

    Example:
        >>> async with TypedClient() as client:                 # doctest: +SKIP
        ...     await authorize(client, TdlibSettings.from_env(), ConsoleCredentials())
        ...     me = await client.get_me()
    """
    return await Authorization(client, settings, credentials).run(timeout=timeout)
