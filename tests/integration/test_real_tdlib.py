"""The stack against a real libtdjson.

Everything else in this suite runs against ``FakeTDJson``, a double written
from the same understanding of TDLib that the library itself was written from.
A shared misunderstanding would pass every one of those tests. These do not
have that problem.

Skipped unless a real library loads and credentials are present::

    TDJSON_PATH=/path/to/libtdjson.so \\
    TDPY_API_ID=... TDPY_API_HASH=... \\
    pytest -m requires_binary

No phone number is ever submitted, so no login code is sent to anyone. The
furthest these go is the point where TDLib asks for one.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from tdpy.api import types
from tdpy.api.client import TypedClient
from tdpy.auth import Authorization, BotCredentials, TdlibSettings
from tdpy.core.errors import TDLibError
from tdpy.helpers.text import parse

pytestmark = pytest.mark.requires_binary


class ReachedPhoneNumber(Exception):
    """Marks that the flow arrived where the test wanted it to stop."""


@pytest.fixture(scope="module", autouse=True)
def _require_library() -> None:
    from tdpy._ffi import TDJsonNotFoundError, load_tdjson

    try:
        load_tdjson()
    except TDJsonNotFoundError as exc:
        pytest.skip(f"no usable libtdjson: {str(exc).splitlines()[0]}")


def credentials() -> tuple[int, str]:
    api_id, api_hash = os.environ.get("TDPY_API_ID"), os.environ.get("TDPY_API_HASH")
    if not api_id or not api_hash:
        pytest.skip("set TDPY_API_ID and TDPY_API_HASH to run the integration tests")
    return int(api_id), api_hash


@pytest.fixture
def offline() -> TypedClient:
    """A client that never starts -- enough for the 28 synchronous methods."""
    from tdpy._ffi import TDJson

    return TypedClient(backend=TDJson())


@pytest.fixture
async def client():
    async with TypedClient(poll_timeout=0.5) as instance:
        yield instance


# ---- the FFI layer, through the real ABI -------------------------------


def test_the_real_library_runs_a_synchronous_method(offline: TypedClient) -> None:
    entities = offline.sync.get_text_entities(text="@durov and #tags")

    kinds = [entity.type.type_name for entity in entities.entities]
    assert "textEntityTypeMention" in kinds
    assert "textEntityTypeHashtag" in kinds


def test_markdown_really_parses(offline: TypedClient) -> None:
    formatted = parse(offline, "*bold* and _italic_", "markdown")

    assert formatted.text == "bold and italic"
    assert [entity.type.type_name for entity in formatted.entities] == [
        "textEntityTypeBold",
        "textEntityTypeItalic",
    ]


def test_malformed_markdown_is_rejected_by_tdlib(offline: TypedClient) -> None:
    # MarkdownV2 reserves '.', so this is an error rather than literal text.
    with pytest.raises(TDLibError):
        parse(offline, "unescaped. dot", "markdown")


# ---- the async core, against real TDLib --------------------------------


async def test_a_request_round_trips(client: TypedClient) -> None:
    # Exercises the receive thread, @extra correlation and typed decoding for
    # real: a request goes out, its reply comes back to its own future.
    version = await client.get_option(name="version")

    assert isinstance(version, types.OptionValueString)
    assert version.value, "TDLib should report its version"


async def test_concurrent_requests_stay_correlated(client: TypedClient) -> None:
    names = ["version", "commit_hash", "version"]
    results = await asyncio.gather(*(client.get_option(name=name) for name in names))

    assert results[0] == results[2], "matching requests must get matching replies"


async def test_a_real_error_maps_to_the_right_exception(client: TypedClient) -> None:
    with pytest.raises(TDLibError) as excinfo:
        await client.get_chat(chat_id=1)

    assert excinfo.value.code >= 400
    assert excinfo.value.request is not None
    assert excinfo.value.request["@type"] == "getChat"


# ---- the authorization state machine -----------------------------------


async def test_the_flow_reaches_the_phone_number_prompt(
    client: TypedClient, tmp_path: Path
) -> None:
    """Drive real TDLib to the point where it asks who is logging in.

    That is as far as it can go without a person: submitting a phone number
    would send a real login code. Reaching ``WaitPhoneNumber`` proves
    ``setTdlibParameters`` was accepted and the state machine is wired right.
    """
    api_id, api_hash = credentials()
    settings = TdlibSettings(
        api_id=api_id,
        api_hash=api_hash,
        database_directory=tmp_path / "td",
        use_message_database=False,
        use_secret_chats=False,
    )

    class StopAtPhoneNumber(BotCredentials):
        """Declines to log in, so the flow halts exactly where we want it."""

        async def login_method(self) -> types.AuthorizationState:
            raise ReachedPhoneNumber

    flow = Authorization(client, settings, StopAtPhoneNumber("unused"))
    with pytest.raises(ReachedPhoneNumber):
        await flow.run(timeout=60)

    assert flow.visited[0] == "authorizationStateWaitTdlibParameters"
    assert flow.visited[-1] == "authorizationStateWaitPhoneNumber"


async def test_tdlib_rejects_empty_credentials(client: TypedClient, tmp_path: Path) -> None:
    # The api_id/api_hash really are checked, and the failure really is a 400.
    with pytest.raises(TDLibError) as excinfo:
        await client.set_tdlib_parameters(
            use_test_dc=False,
            database_directory=str(tmp_path / "rejected"),
            files_directory=str(tmp_path / "rejected"),
            database_encryption_key=b"",
            use_file_database=False,
            use_chat_info_database=False,
            use_message_database=False,
            use_secret_chats=False,
            api_id=0,
            api_hash="",
            system_language_code="en",
            device_model="tdpy",
            system_version="test",
            application_version="test",
        )

    assert excinfo.value.code == 400
