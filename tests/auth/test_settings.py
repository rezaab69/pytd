from __future__ import annotations

from pathlib import Path

import pytest

from tdpy.auth.settings import MissingCredentialsError, TdlibSettings


def test_required_fields_reach_the_request() -> None:
    request = TdlibSettings(api_id=7, api_hash="h").to_request()

    assert request.type_name == "setTdlibParameters"
    assert request.api_id == 7
    assert request.api_hash == "h"


def test_message_database_implies_the_others() -> None:
    # TDLib turns these on regardless; saying so keeps the object honest.
    settings = TdlibSettings(
        api_id=1, api_hash="h", use_file_database=False, use_chat_info_database=False
    ).normalized()

    assert settings.use_chat_info_database
    assert settings.use_file_database


def test_disabling_everything_stays_disabled() -> None:
    settings = TdlibSettings(
        api_id=1,
        api_hash="h",
        use_file_database=False,
        use_chat_info_database=False,
        use_message_database=False,
    ).normalized()

    assert not settings.use_file_database
    assert not settings.use_chat_info_database


def test_files_default_to_the_database_directory() -> None:
    request = TdlibSettings(api_id=1, api_hash="h", database_directory=Path("s")).to_request()

    assert request.database_directory == "s"
    assert request.files_directory == "s"


def test_system_version_is_filled_in() -> None:
    # TDLib rejects an empty system_version, so it cannot be left blank.
    assert TdlibSettings(api_id=1, api_hash="h").to_request().system_version


def test_an_explicit_system_version_is_kept() -> None:
    settings = TdlibSettings(api_id=1, api_hash="h", system_version="Debian 12")

    assert settings.to_request().system_version == "Debian 12"


def test_settings_are_immutable() -> None:
    with pytest.raises(AttributeError):
        TdlibSettings(api_id=1, api_hash="h").api_id = 2  # type: ignore[misc]


def test_from_env_reads_the_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDPY_API_ID", "42")
    monkeypatch.setenv("TDPY_API_HASH", "cafe")
    monkeypatch.setenv("TDPY_DATABASE_DIRECTORY", "/tmp/session")

    settings = TdlibSettings.from_env()

    assert settings.api_id == 42
    assert settings.api_hash == "cafe"
    assert settings.database_directory == Path("/tmp/session")


def test_from_env_names_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TDPY_API_ID", raising=False)
    monkeypatch.setenv("TDPY_API_HASH", "cafe")

    with pytest.raises(MissingCredentialsError, match="TDPY_API_ID"):
        TdlibSettings.from_env()


def test_from_env_rejects_a_non_numeric_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDPY_API_ID", "not-a-number")
    monkeypatch.setenv("TDPY_API_HASH", "cafe")

    with pytest.raises(MissingCredentialsError, match="must be an integer"):
        TdlibSettings.from_env()


def test_from_env_accepts_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDPY_API_ID", "42")
    monkeypatch.setenv("TDPY_API_HASH", "cafe")

    assert TdlibSettings.from_env(use_test_dc=True).use_test_dc


def test_the_encryption_key_is_sent_as_bytes() -> None:
    request = TdlibSettings(api_id=1, api_hash="h", database_encryption_key=b"secret").to_request()

    assert request.database_encryption_key == b"secret"
