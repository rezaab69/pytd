from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tdpy._ffi.loader import (
    TDJsonNotFoundError,
    iter_candidates,
    library_names,
    load_tdjson,
)


def test_library_names_match_the_platform() -> None:
    names = library_names()
    assert names
    suffix = {"win32": ".dll", "darwin": ".dylib"}.get(sys.platform, ".so")
    assert any(suffix in name for name in names)


def test_explicit_path_disables_every_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # An explicit choice that is wrong must fail loudly rather than quietly
    # loading some other TDLib build that happens to be installed.
    monkeypatch.setenv("TDJSON_PATH", "/should/be/ignored")
    assert [Path(c) for c in iter_candidates("/only/this.so")] == [Path("/only/this.so")]


def test_env_var_is_tried_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDJSON_PATH", "/from/env.so")
    assert Path(next(iter_candidates())) == Path("/from/env.so")


def test_directory_is_expanded_to_known_filenames(tmp_path: Path) -> None:
    planted = tmp_path / library_names()[0]
    planted.touch()

    assert str(planted) in list(iter_candidates(tmp_path))


def test_directory_without_a_library_yields_nothing(tmp_path: Path) -> None:
    assert list(iter_candidates(tmp_path)) == []


def test_bare_names_are_offered_to_the_os_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TDJSON_PATH", raising=False)
    candidates = list(iter_candidates())
    assert set(library_names()) <= set(candidates)


def test_missing_library_raises_an_actionable_error(tmp_path: Path) -> None:
    missing = tmp_path / library_names()[0]

    with pytest.raises(TDJsonNotFoundError) as excinfo:
        load_tdjson(missing)

    message = str(excinfo.value)
    assert str(missing) in message
    assert "no such file" in message
    assert "TDJSON_PATH" in message  # the error has to say how to fix itself
    assert excinfo.value.attempts == [(str(missing), "no such file")]


def test_a_non_library_file_reports_the_os_error(tmp_path: Path) -> None:
    impostor = tmp_path / library_names()[0]
    impostor.write_bytes(b"not a shared library")

    with pytest.raises(TDJsonNotFoundError) as excinfo:
        load_tdjson(impostor)

    assert excinfo.value.attempts[0][0] == str(impostor)
    assert "no such file" not in excinfo.value.attempts[0][1]
