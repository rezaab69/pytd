from __future__ import annotations

import pytest

from tdpy._ffi import FakeTDJson
from tdpy.api.client import TypedClient

CLOSED = {
    "@type": "updateAuthorizationState",
    "authorization_state": {"@type": "authorizationStateClosed"},
}


@pytest.fixture
async def client(fake: FakeTDJson):
    fake.on("close", lambda _req: CLOSED)
    async with TypedClient(backend=fake, poll_timeout=0.05) as instance:
        yield instance
