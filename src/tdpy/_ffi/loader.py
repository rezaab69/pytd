"""Locate and ``dlopen`` the TDLib ``tdjson`` shared library.

TDLib publishes no official Python wheels, so the library has to be found on
the user's machine. The cost of getting this wrong is a stack trace that says
nothing useful, so the search order is explicit and every failed candidate is
kept for the error message.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import importlib.util
import os
import sys
from collections.abc import Iterator
from pathlib import Path

from tdpy.exceptions import TdpyError

__all__ = ["TDJsonNotFoundError", "iter_candidates", "library_names", "load_tdjson"]

_ENV_VAR = "TDJSON_PATH"

_INSTALL_HINTS = {
    "win32": (
        "Download a prebuilt tdjson.dll from https://github.com/tdlib/td#building\n"
        "  or build it with vcpkg, then either place it next to your script,\n"
        "  add its folder to PATH, or set TDJSON_PATH to the .dll itself.\n"
        "  The DLL needs its OpenSSL and zlib siblings in the same folder."
    ),
    "darwin": "brew install tdlib   (then TDJSON_PATH is usually unnecessary)",
    "linux": (
        "Build from source (https://tdlib.github.io/td/build.html) and run\n"
        "  `sudo ldconfig`, or set TDJSON_PATH=/path/to/libtdjson.so"
    ),
}

# Directories worth probing when the OS loader comes up empty. Ordered
# most-specific first so a purpose-built TDLib wins over a distro package.
_POSIX_DIRS = (
    "/usr/local/opt/tdlib/lib",
    "/opt/homebrew/opt/tdlib/lib",
    "/opt/homebrew/lib",
    "/usr/local/lib",
    "/usr/lib64",
    "/usr/lib/x86_64-linux-gnu",
    "/usr/lib/aarch64-linux-gnu",
    "/usr/lib",
)

# Windows DLL directories stay registered for the life of the process; keeping
# the cookies alive here also stops us re-adding the same directory twice.
_dll_directories: dict[str, object] = {}


class TDJsonNotFoundError(TdpyError):
    """Raised when no loadable ``tdjson`` library could be found."""

    def __init__(self, attempts: list[tuple[str, str]]) -> None:
        self.attempts = attempts
        super().__init__(self._format(attempts))

    @staticmethod
    def _format(attempts: list[tuple[str, str]]) -> str:
        hint = _INSTALL_HINTS.get(sys.platform, _INSTALL_HINTS["linux"])
        lines = [
            "Could not load TDLib's tdjson shared library.",
            "",
            f"Looked for {', '.join(library_names())} in this order:",
        ]
        if attempts:
            lines += [f"  - {spec}\n      {error}" for spec, error in attempts]
        else:
            lines.append("  (no candidates were produced)")
        lines += ["", f"To install it on {sys.platform}:", f"  {hint}"]
        return "\n".join(lines)


def library_names() -> tuple[str, ...]:
    """Shared-library filenames to try on the current platform."""
    if sys.platform == "win32":
        return ("tdjson.dll", "libtdjson.dll")
    if sys.platform == "darwin":
        return ("libtdjson.dylib", "libtdjson.1.dylib")
    return ("libtdjson.so", "libtdjson.so.1")


def _from_path_like(value: str | Path) -> Iterator[str]:
    """Expand a user-supplied file *or* directory into loadable candidates."""
    path = Path(value).expanduser()
    if path.is_dir():
        for name in library_names():
            candidate = path / name
            if candidate.exists():
                yield str(candidate)
    else:
        yield str(path)


def _packaged_candidates() -> Iterator[str]:
    """Look inside an installed ``tdjson`` distribution, if the user has one.

    Several third-party projects ship prebuilt binaries under that import name.
    Best-effort: a missing or oddly-shaped package is simply skipped.
    """
    try:
        spec = importlib.util.find_spec("tdjson")
    except (ImportError, ValueError):
        return
    if spec is None or not spec.submodule_search_locations:
        return
    for location in spec.submodule_search_locations:
        root = Path(location)
        for name in library_names():
            yield from (str(p) for p in root.rglob(name))


def iter_candidates(explicit: str | Path | None = None) -> Iterator[str]:
    """Yield library specs to try, most authoritative first.

    A spec is either an absolute path or a bare filename; bare names are handed
    to the OS loader so ``PATH`` / ``LD_LIBRARY_PATH`` / ``DYLD_LIBRARY_PATH``
    still work the way users expect.

    Naming a library -- by argument or by ``TDJSON_PATH`` -- suppresses every
    other candidate. Only an unguided search falls back.
    """
    if explicit is not None:
        yield from _from_path_like(explicit)
        return  # An explicit request must not silently fall back to something else.

    env_value = os.environ.get(_ENV_VAR)
    if env_value:
        # As deliberate as the argument, and treated the same way. If the named
        # library will not load, falling through to a system copy would test a
        # different build than the one that was pointed at -- and the symptom
        # is silence, not an error.
        yield from _from_path_like(env_value)
        return

    yield from _packaged_candidates()

    found = ctypes.util.find_library("tdjson")
    if found:
        yield found

    yield from library_names()

    search_dirs = list(_POSIX_DIRS)
    if sys.platform == "win32":
        search_dirs = [str(Path(sys.prefix) / "Library" / "bin"), str(Path.cwd())]
    for directory in search_dirs:
        for name in library_names():
            candidate = Path(directory) / name
            if candidate.exists():
                yield str(candidate)


def _register_dll_directory(path: Path) -> None:
    """Let Windows resolve libcrypto/zlib sitting next to tdjson.dll."""
    if sys.platform != "win32":
        return
    parent = str(path.parent)
    if parent in _dll_directories or not path.parent.is_dir():
        return
    # A directory Windows refuses to index is not fatal; the load may still work.
    with contextlib.suppress(OSError):
        _dll_directories[parent] = os.add_dll_directory(parent)


def load_tdjson(path: str | Path | None = None) -> tuple[ctypes.CDLL, str]:
    """Load the library and return it alongside the spec that worked.

    Args:
        path: An exact library file or a directory to search. When given, no
            fallback candidates are tried -- an explicit choice that fails
            should fail loudly rather than silently loading a different build.

    Raises:
        TDJsonNotFoundError: if every candidate failed, listing each attempt.
    """
    attempts: list[tuple[str, str]] = []
    for spec in iter_candidates(path):
        candidate = Path(spec)
        if candidate.is_absolute():
            if not candidate.exists():
                attempts.append((spec, "no such file"))
                continue
            _register_dll_directory(candidate)
        try:
            return ctypes.CDLL(spec), spec
        except OSError as exc:
            attempts.append((spec, str(exc)))
    raise TDJsonNotFoundError(attempts)
