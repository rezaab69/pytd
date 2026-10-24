from __future__ import annotations

from pathlib import Path

import pytest

from tdpy._envfile import ENV_FILE_VAR
from tdpy._ffi import FakeTDJson

_INTEGRATION = Path(__file__).parent / "integration"


@pytest.fixture(autouse=True)
def _no_env_file(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's own .env from leaking into unit tests.

    The integration tests are the exception: reading real credentials from
    that file is exactly what they want.
    """
    if _INTEGRATION not in Path(request.node.path).parents:
        monkeypatch.setenv(ENV_FILE_VAR, str(Path(__file__).parent / "no-such.env"))


@pytest.fixture
def fake() -> FakeTDJson:
    backend = FakeTDJson()
    yield backend
    backend.close()
