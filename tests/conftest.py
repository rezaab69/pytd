from __future__ import annotations

import pytest

from tdpy._ffi import FakeTDJson


@pytest.fixture
def fake() -> FakeTDJson:
    backend = FakeTDJson()
    yield backend
    backend.close()
