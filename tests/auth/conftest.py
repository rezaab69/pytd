from __future__ import annotations

from typing import Any

import pytest

from tdpy._ffi import FakeTDJson
from tdpy.api.client import TypedClient
from tdpy.auth.settings import TdlibSettings

CLOSED = {
    "@type": "updateAuthorizationState",
    "authorization_state": {"@type": "authorizationStateClosed"},
}


def state_update(name: str, **fields: Any) -> dict[str, Any]:
    """An updateAuthorizationState carrying the named state."""
    return {
        "@type": "updateAuthorizationState",
        "authorization_state": {"@type": name, **fields},
    }


class Script:
    """Walks the fake through a fixed sequence of authorization states.

    Each answered request advances TDLib one step, which is how the real thing
    behaves: you reply, and the next state arrives as an update.
    """

    def __init__(self, fake: FakeTDJson, states: list[dict[str, Any]]) -> None:
        self.fake = fake
        self.states = list(states)
        self.client_id = 1
        fake.on("getAuthorizationState", self._advance)

    def _advance(self, _request: dict[str, Any]) -> None:
        self.push_next()
        return None

    def push_next(self) -> None:
        if self.states:
            self.fake.push(self.states.pop(0), client_id=self.client_id)

    def answers(self, *methods: str, reply: dict[str, Any] | None = None) -> None:
        """Reply `ok` to each method and move to the next state."""

        def respond(_request: dict[str, Any]) -> dict[str, Any]:
            self.push_next()
            return reply or {"@type": "ok"}

        for method in methods:
            self.fake.on(method, respond)


@pytest.fixture
def script(fake: FakeTDJson):
    """Factory for a scripted authorization sequence."""

    def make(states: list[dict[str, Any]]) -> Script:
        return Script(fake, states)

    return make


@pytest.fixture
def settings() -> TdlibSettings:
    return TdlibSettings(api_id=12345, api_hash="abcdef", database_directory="test_session")


@pytest.fixture
async def client(fake: FakeTDJson):
    fake.on("close", lambda _req: CLOSED)
    async with TypedClient(backend=fake, poll_timeout=0.05) as instance:
        yield instance
