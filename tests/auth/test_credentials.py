from __future__ import annotations

import pytest

from tdpy.api import types
from tdpy.auth.credentials import (
    BotCredentials,
    BotToken,
    CallbackCredentials,
    ConsoleCredentials,
    EnvCredentials,
    PhoneNumber,
    QrCode,
    Registration,
    UnsupportedStepError,
)

TERMS = types.TermsOfService(text=types.FormattedText())
CODE_INFO = types.AuthenticationCodeInfo(
    phone_number="+15551234", type=types.AuthenticationCodeTypeSms(length=5)
)


async def test_bot_credentials_supply_only_a_token() -> None:
    assert await BotCredentials("123:ABC").login_method() == BotToken("123:ABC")


@pytest.mark.parametrize(
    "step",
    ["authentication_code", "password", "email_address", "email_code", "registration"],
)
async def test_unanswerable_steps_name_themselves(step: str) -> None:
    # The alternative is a None arriving three frames later with no clue why.
    credentials = BotCredentials("t")
    argument = {
        "authentication_code": CODE_INFO,
        "password": types.AuthorizationStateWaitPassword(),
        "email_code": types.AuthorizationStateWaitEmailCode(
            code_info=types.EmailAddressAuthenticationCodeInfo()
        ),
        "registration": TERMS,
    }.get(step)

    with pytest.raises(UnsupportedStepError, match="BotCredentials"):
        method = getattr(credentials, step)
        await (method() if argument is None else method(argument))


async def test_console_credentials_can_skip_the_phone_prompt() -> None:
    assert await ConsoleCredentials(phone="+15551234").login_method() == PhoneNumber("+15551234")


async def test_console_credentials_prefer_a_bot_token() -> None:
    credentials = ConsoleCredentials(phone="+1", bot_token="123:ABC")

    assert await credentials.login_method() == BotToken("123:ABC")


async def test_console_prompts_run_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    # input() blocks; on the loop thread it would stall the very updates that
    # tell us the code was accepted.
    import asyncio
    import threading

    loop_thread = threading.get_ident()
    seen: list[int] = []

    def fake_input(prompt: str = "") -> str:
        seen.append(threading.get_ident())
        return "  12345  "

    monkeypatch.setattr("builtins.input", fake_input)
    code = await ConsoleCredentials().authentication_code(CODE_INFO)

    assert code == "12345", "answers are stripped"
    assert seen and seen[0] != loop_thread
    assert asyncio.get_running_loop() is not None


async def test_the_code_prompt_says_where_the_code_went(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "1")

    await ConsoleCredentials().authentication_code(CODE_INFO)

    assert "SMS" in prompts[0]
    assert "+15551234" in prompts[0]


async def test_env_credentials_prefer_a_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDPY_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TDPY_PHONE", "+1")

    assert await EnvCredentials().login_method() == BotToken("123:ABC")


async def test_env_credentials_fall_back_to_a_phone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TDPY_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TDPY_PHONE", "+15551234")

    assert await EnvCredentials().login_method() == PhoneNumber("+15551234")


async def test_env_credentials_delegate_interactive_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TDPY_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TDPY_PHONE", raising=False)
    monkeypatch.delenv("TDPY_PASSWORD", raising=False)
    fallback = CallbackCredentials(
        login_method=lambda: _wrap(PhoneNumber("+1")),
        authentication_code=lambda _info: _wrap("9999"),
    )
    credentials = EnvCredentials(fallback=fallback)

    assert await credentials.login_method() == PhoneNumber("+1")
    assert await credentials.authentication_code(CODE_INFO) == "9999"


async def test_env_password_beats_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDPY_PASSWORD", "from-env")
    fallback = CallbackCredentials(
        login_method=lambda: _wrap(QrCode()), password=lambda _s: _wrap("from-fallback")
    )

    result = await EnvCredentials(fallback=fallback).password(
        types.AuthorizationStateWaitPassword()
    )

    assert result == "from-env"


async def test_env_credentials_without_anything_say_what_to_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TDPY_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TDPY_PHONE", raising=False)

    with pytest.raises(UnsupportedStepError, match="TDPY_BOT_TOKEN"):
        await EnvCredentials().login_method()


async def test_callback_credentials_fall_through_to_the_base_error() -> None:
    credentials = CallbackCredentials(login_method=lambda: _wrap(QrCode()))

    with pytest.raises(UnsupportedStepError, match="2FA password"):
        await credentials.password(types.AuthorizationStateWaitPassword())


async def test_callback_registration_is_used_when_given() -> None:
    credentials = CallbackCredentials(
        login_method=lambda: _wrap(QrCode()),
        registration=lambda _terms: _wrap(Registration(first_name="Ada")),
    )

    assert (await credentials.registration(TERMS)).first_name == "Ada"


async def _wrap(value):
    return value


async def test_console_password_uses_getpass(monkeypatch: pytest.MonkeyPatch) -> None:
    # A password typed at a visible prompt ends up in scrollback and history.
    prompts: list[str] = []
    monkeypatch.setattr("getpass.getpass", lambda prompt="": prompts.append(prompt) or " pw ")
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("must not echo"))

    state = types.AuthorizationStateWaitPassword(password_hint="first pet")
    result = await ConsoleCredentials().password(state)

    assert result == "pw"
    assert "first pet" in prompts[0], "the hint is the point of showing it"


async def test_console_password_without_a_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("getpass.getpass", lambda prompt="": prompts.append(prompt) or "pw")

    await ConsoleCredentials().password(types.AuthorizationStateWaitPassword())

    assert "hint" not in prompts[0]


async def test_console_registration_shows_the_terms(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = iter(["Ada", "Lovelace"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    terms = types.TermsOfService(text=types.FormattedText(text="Be excellent."))

    result = await ConsoleCredentials().registration(terms)

    assert result == Registration(first_name="Ada", last_name="Lovelace")
    assert "Be excellent." in capsys.readouterr().out


async def test_console_email_prompt_names_the_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "1")
    state = types.AuthorizationStateWaitEmailCode(
        code_info=types.EmailAddressAuthenticationCodeInfo(email_address_pattern="a**@b.com")
    )

    await ConsoleCredentials().email_code(state)

    assert "a**@b.com" in prompts[0]


async def test_console_email_address_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": " ada@example.com ")

    assert await ConsoleCredentials().email_address() == "ada@example.com"


async def test_console_phone_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": "+15551234")

    assert await ConsoleCredentials().login_method() == PhoneNumber("+15551234")


async def test_console_shows_the_qr_link(capsys: pytest.CaptureFixture[str]) -> None:
    await ConsoleCredentials().show_qr_link("tg://login?token=x")

    assert "tg://login?token=x" in capsys.readouterr().out


async def test_an_unknown_code_type_still_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "1")
    info = types.AuthenticationCodeInfo(type=types.AuthenticationCodeTypeFragment())

    await ConsoleCredentials().authentication_code(info)

    assert prompts[0].startswith("Login code")
