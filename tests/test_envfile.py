from __future__ import annotations

from pathlib import Path

import pytest

from tdpy._envfile import ENV_FILE_VAR, getenv, read_env_file
from tdpy._ffi.loader import iter_candidates
from tdpy.auth import EnvCredentials, TdlibSettings
from tdpy.auth.credentials import BotToken


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".env"
    monkeypatch.setenv(ENV_FILE_VAR, str(path))
    for name in ("TDJSON_PATH", "TDPY_API_ID", "TDPY_API_HASH", "TDPY_BOT_TOKEN", "TDPY_PHONE"):
        monkeypatch.delenv(name, raising=False)
    return path


def test_parses_comments_quotes_and_export(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# credentials\n"
        "\n"
        "export TDPY_API_ID=42\n"
        'TDPY_API_HASH="ca fe"\n'
        "TDPY_BOT_TOKEN='123:ABC' \n"
        "TDJSON_PATH=/opt/td/libtdjson.so  # built locally\n"
        "not a pair\n",
        encoding="utf-8",
    )

    assert read_env_file(path) == {
        "TDPY_API_ID": "42",
        "TDPY_API_HASH": "ca fe",
        "TDPY_BOT_TOKEN": "123:ABC",
        "TDJSON_PATH": "/opt/td/libtdjson.so",
    }


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_env_file(tmp_path / "absent.env") == {}


def test_exported_variable_wins(env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file.write_text("TDPY_API_ID=1\n", encoding="utf-8")
    monkeypatch.setenv("TDPY_API_ID", "2")

    assert getenv("TDPY_API_ID") == "2"


def test_all_four_settings_come_from_the_file(env_file: Path) -> None:
    env_file.write_text(
        "TDJSON_PATH=/from/file.so\nTDPY_API_ID=42\nTDPY_API_HASH=cafe\nTDPY_BOT_TOKEN=123:ABC\n",
        encoding="utf-8",
    )

    settings = TdlibSettings.from_env()
    assert (settings.api_id, settings.api_hash) == (42, "cafe")
    assert list(iter_candidates()) == [str(Path("/from/file.so"))]


async def test_bot_token_comes_from_the_file(env_file: Path) -> None:
    env_file.write_text("TDPY_BOT_TOKEN=123:ABC\n", encoding="utf-8")

    assert await EnvCredentials().login_method() == BotToken("123:ABC")
