"""Every branch of the authorization state machine, with no network."""

from __future__ import annotations

import asyncio

import pytest

from tdpy._ffi import FakeTDJson
from tdpy.api.client import TypedClient
from tdpy.auth import (
    Authorization,
    AuthorizationError,
    BotCredentials,
    CallbackCredentials,
    PhoneNumber,
    QrCode,
    Registration,
    SessionClosedError,
    TdlibSettings,
    UnsupportedStepError,
    authorize,
)


def state_update(name: str, **fields: object) -> dict[str, object]:
    """An updateAuthorizationState carrying the named state."""
    return {
        "@type": "updateAuthorizationState",
        "authorization_state": {"@type": name, **fields},
    }


CODE_INFO = {
    "@type": "authenticationCodeInfo",
    "phone_number": "+15551234",
    "type": {"@type": "authenticationCodeTypeSms", "length": 5},
}


def phone_credentials(**overrides):
    defaults = {
        "login_method": lambda: _async(PhoneNumber("+15551234")),
        "authentication_code": lambda _info: _async("12345"),
        "password": lambda _state: _async("hunter2"),
    }
    return CallbackCredentials(**{**defaults, **overrides})


async def _async(value):
    return value


async def test_bot_login_reaches_ready(
    client: TypedClient, fake: FakeTDJson, settings: TdlibSettings, script
) -> None:
    scripted = script(
        [
            state_update("authorizationStateWaitTdlibParameters"),
            state_update("authorizationStateWaitPhoneNumber"),
            state_update("authorizationStateReady"),
        ]
    )
    scripted.answers("setTdlibParameters", "checkAuthenticationBotToken")

    flow = Authorization(client, settings, BotCredentials("123:ABC"))
    await flow.run(timeout=5)

    assert flow.visited == (
        "authorizationStateWaitTdlibParameters",
        "authorizationStateWaitPhoneNumber",
        "authorizationStateReady",
    )
    assert fake.sent_of("checkAuthenticationBotToken")[0]["token"] == "123:ABC"


async def test_tdlib_parameters_are_sent_from_the_settings(
    client: TypedClient, fake: FakeTDJson, settings: TdlibSettings, script
) -> None:
    scripted = script(
        [
            state_update("authorizationStateWaitTdlibParameters"),
            state_update("authorizationStateReady"),
        ]
    )
    scripted.answers("setTdlibParameters")

    await authorize(client, settings, BotCredentials("t"), timeout=5)
    sent = fake.sent_of("setTdlibParameters")[0]

    assert sent["api_id"] == 12345
    assert sent["api_hash"] == "abcdef"
    assert sent["database_directory"] == "test_session"
    assert sent["system_version"], "TDLib rejects an empty system_version"


async def test_phone_login_with_a_code(
    client: TypedClient, fake: FakeTDJson, settings: TdlibSettings, script
) -> None:
    scripted = script(
        [
            state_update("authorizationStateWaitPhoneNumber"),
            state_update("authorizationStateWaitCode", code_info=CODE_INFO),
            state_update("authorizationStateReady"),
        ]
    )
    scripted.answers("setAuthenticationPhoneNumber", "checkAuthenticationCode")

    await authorize(client, settings, phone_credentials(), timeout=5)

    assert fake.sent_of("setAuthenticationPhoneNumber")[0]["phone_number"] == "+15551234"
    assert fake.sent_of("checkAuthenticationCode")[0]["code"] == "12345"


async def test_two_factor_password_step(
    client: TypedClient, fake: FakeTDJson, settings: TdlibSettings, script
) -> None:
    scripted = script(
        [
            state_update("authorizationStateWaitPhoneNumber"),
            state_update("authorizationStateWaitCode", code_info=CODE_INFO),
            state_update(
                "authorizationStateWaitPassword",
                password_hint="the usual",
                has_recovery_email_address=True,
            ),
            state_update("authorizationStateReady"),
        ]
    )
    scripted.answers(
        "setAuthenticationPhoneNumber", "checkAuthenticationCode", "checkAuthenticationPassword"
    )
    seen_hint = []

    credentials = phone_credentials(
        password=lambda state: _remember(seen_hint, state.password_hint, "hunter2")
    )
    flow = Authorization(client, settings, credentials)
    await flow.run(timeout=5)

    assert fake.sent_of("checkAuthenticationPassword")[0]["password"] == "hunter2"
    assert seen_hint == ["the usual"], "the hint must reach the provider"
    assert "authorizationStateWaitPassword" in flow.visited


async def _remember(sink, value, result):
    sink.append(value)
    return result


async def test_qr_login_shows_the_link_and_waits(
    client: TypedClient, fake: FakeTDJson, settings: TdlibSettings, script
) -> None:
    # TDLib sends nothing back at WaitOtherDeviceConfirmation: it advances on
    # its own once the code is scanned. Showing the link is the whole step.
    scripted = script(
        [
            state_update("authorizationStateWaitPhoneNumber"),
            state_update(
                "authorizationStateWaitOtherDeviceConfirmation", link="tg://login?token=x"
            ),
            state_update("authorizationStateReady"),
        ]
    )
    scripted.answers("requestQrCodeAuthentication")
    shown: list[str] = []

    async def scan(link: str) -> None:
        shown.append(link)
        scripted.push_next()  # the user scans, and TDLib moves on

    credentials = CallbackCredentials(login_method=lambda: _async(QrCode()), show_qr_link=scan)
    flow = Authorization(client, settings, credentials)
    await flow.run(timeout=5)

    assert shown == ["tg://login?token=x"]
    assert "authorizationStateWaitOtherDeviceConfirmation" in flow.visited
    # An empty vector is the default, so it stays off the wire; TDLib reads an
    # absent vector as empty.
    assert "other_user_ids" not in fake.sent_of("requestQrCodeAuthentication")[0]


async def test_registration_of_a_new_account(
    client: TypedClient, fake: FakeTDJson, settings: TdlibSettings, script
) -> None:
    scripted = script(
        [
            state_update("authorizationStateWaitPhoneNumber"),
            state_update(
                "authorizationStateWaitRegistration",
                terms_of_service={
                    "@type": "termsOfService",
                    "text": {"@type": "formattedText", "text": "Be nice."},
                },
            ),
            state_update("authorizationStateReady"),
        ]
    )
    scripted.answers("setAuthenticationPhoneNumber", "registerUser")
    seen_terms: list[str] = []

    credentials = phone_credentials(
        registration=lambda terms: _remember(
            seen_terms, terms.text.text, Registration(first_name="Ada", last_name="Lovelace")
        )
    )
    await authorize(client, settings, credentials, timeout=5)
    sent = fake.sent_of("registerUser")[0]

    assert sent["first_name"] == "Ada"
    assert sent["last_name"] == "Lovelace"
    assert seen_terms == ["Be nice."], "the terms must reach the provider"


async def test_email_address_and_code_steps(
    client: TypedClient, fake: FakeTDJson, settings: TdlibSettings, script
) -> None:
    scripted = script(
        [
            state_update("authorizationStateWaitPhoneNumber"),
            state_update("authorizationStateWaitEmailAddress"),
            state_update(
                "authorizationStateWaitEmailCode",
                code_info={
                    "@type": "emailAddressAuthenticationCodeInfo",
                    "email_address_pattern": "a**@b.com",
                    "length": 6,
                },
            ),
            state_update("authorizationStateReady"),
        ]
    )
    scripted.answers(
        "setAuthenticationPhoneNumber",
        "setAuthenticationEmailAddress",
        "checkAuthenticationEmailCode",
    )

    credentials = phone_credentials(
        email_address=lambda: _async("ada@example.com"),
        email_code=lambda _state: _async("424242"),
    )
    await authorize(client, settings, credentials, timeout=5)

    assert fake.sent_of("setAuthenticationEmailAddress")[0]["email_address"] == "ada@example.com"
    # The code is wrapped in the EmailAddressAuthentication union, not sent raw.
    sent = fake.sent_of("checkAuthenticationEmailCode")[0]
    assert sent["code"] == {"@type": "emailAddressAuthenticationCode", "code": "424242"}


async def test_a_closing_session_fails_the_flow(
    client: TypedClient, fake: FakeTDJson, settings: TdlibSettings, script
) -> None:
    script([state_update("authorizationStateClosed")])

    with pytest.raises(SessionClosedError, match="authorizationStateClosed"):
        await authorize(client, settings, BotCredentials("t"), timeout=5)


async def test_premium_purchase_is_reported_as_unsupported(
    client: TypedClient, fake: FakeTDJson, settings: TdlibSettings, script
) -> None:
    script([state_update("authorizationStateWaitPremiumPurchase", store_product_id="p")])

    with pytest.raises(AuthorizationError, match="Premium"):
        await authorize(client, settings, BotCredentials("t"), timeout=5)


async def test_a_provider_that_cannot_answer_says_which_step(
    client: TypedClient, fake: FakeTDJson, settings: TdlibSettings, script
) -> None:
    # A bot provider reaching a code prompt means something is wrong; the error
    # has to name the step rather than surfacing as a None somewhere later.
    scripted = script(
        [
            state_update("authorizationStateWaitPhoneNumber"),
            state_update("authorizationStateWaitCode", code_info=CODE_INFO),
        ]
    )
    scripted.answers("checkAuthenticationBotToken")

    with pytest.raises(UnsupportedStepError, match="login code"):
        await authorize(client, settings, BotCredentials("t"), timeout=5)


async def test_qr_login_can_name_other_users(
    client: TypedClient, fake: FakeTDJson, settings: TdlibSettings, script
) -> None:
    scripted = script(
        [
            state_update("authorizationStateWaitPhoneNumber"),
            state_update("authorizationStateReady"),
        ]
    )
    scripted.answers("requestQrCodeAuthentication")

    await authorize(
        client,
        settings,
        CallbackCredentials(login_method=lambda: _async(QrCode(other_user_ids=[7, 9]))),
        timeout=5,
    )

    assert fake.sent_of("requestQrCodeAuthentication")[0]["other_user_ids"] == [7, 9]


async def test_tdlib_errors_propagate_from_a_step(
    client: TypedClient, fake: FakeTDJson, settings: TdlibSettings, script
) -> None:
    from tdpy.core.errors import BadRequest

    script(
        [
            state_update("authorizationStateWaitPhoneNumber"),
            state_update("authorizationStateWaitCode", code_info=CODE_INFO),
        ]
    )
    fake.on("setAuthenticationPhoneNumber", {"@type": "ok"})
    fake.on(
        "checkAuthenticationCode",
        {"@type": "error", "code": 400, "message": "PHONE_CODE_INVALID"},
    )
    fake.push(state_update("authorizationStateWaitCode", code_info=CODE_INFO), client_id=1)

    with pytest.raises(BadRequest, match="PHONE_CODE_INVALID"):
        await authorize(client, settings, phone_credentials(), timeout=5)


async def test_the_flow_leaves_the_update_stream_alone(
    client: TypedClient, fake: FakeTDJson, settings: TdlibSettings, script
) -> None:
    # The flow observes rather than consumes, so an application reading updates
    # across start-up still sees everything, auth states included.
    scripted = script(
        [
            state_update("authorizationStateWaitPhoneNumber"),
            state_update("authorizationStateReady"),
        ]
    )
    scripted.answers("checkAuthenticationBotToken")
    fake.push({"@type": "updateOption", "name": "version"}, client_id=1)

    await authorize(client, settings, BotCredentials("t"), timeout=5)

    seen = []
    async for update in client.updates():
        seen.append(update["@type"])
        if len(seen) == 3:
            break

    assert "updateOption" in seen
    assert seen.count("updateAuthorizationState") == 2


async def test_unsubscribes_even_when_a_step_fails(
    client: TypedClient, fake: FakeTDJson, settings: TdlibSettings, script
) -> None:
    script([state_update("authorizationStateWaitPremiumPurchase")])

    with pytest.raises(AuthorizationError):
        await authorize(client, settings, BotCredentials("t"), timeout=5)

    assert client._observers == [], "a failed flow must not leave an observer behind"


async def test_timeout_gives_up_rather_than_hanging(
    client: TypedClient, settings: TdlibSettings
) -> None:
    # asyncio.TimeoutError only became an alias of the builtin in 3.11, so
    # the asyncio one is what works on every supported version.
    with pytest.raises(asyncio.TimeoutError):
        await authorize(client, settings, BotCredentials("t"), timeout=0.05)
