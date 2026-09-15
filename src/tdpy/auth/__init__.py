"""Logging in: the authorization state machine and where its answers come from."""

from __future__ import annotations

from tdpy.auth.credentials import (
    BotCredentials,
    BotToken,
    CallbackCredentials,
    ConsoleCredentials,
    Credentials,
    EnvCredentials,
    LoginMethod,
    PhoneNumber,
    QrCode,
    Registration,
    UnsupportedStepError,
)
from tdpy.auth.flow import Authorization, AuthorizationError, SessionClosedError, authorize
from tdpy.auth.settings import MissingCredentialsError, TdlibSettings

__all__ = [
    "Authorization",
    "AuthorizationError",
    "BotCredentials",
    "BotToken",
    "CallbackCredentials",
    "ConsoleCredentials",
    "Credentials",
    "EnvCredentials",
    "LoginMethod",
    "MissingCredentialsError",
    "PhoneNumber",
    "QrCode",
    "Registration",
    "SessionClosedError",
    "TdlibSettings",
    "UnsupportedStepError",
    "authorize",
]
