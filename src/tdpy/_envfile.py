"""Settings read from the environment, or from a ``.env`` file beside the app.

Every ``TDPY_*`` and ``TDJSON_PATH`` lookup goes through :func:`getenv`, so an
application can keep its credentials in one file instead of exporting them in
every shell. A variable that is really set always wins over the file: that is
what lets CI and one-off overrides work without editing it.

The file is ``.env`` in the current working directory, or whatever
``TDPY_ENV_FILE`` names. A missing file is not an error.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["ENV_FILE_VAR", "env_file_path", "getenv", "read_env_file"]

#: Points tdpy at a different file. Read from the real environment only.
ENV_FILE_VAR = "TDPY_ENV_FILE"

_DEFAULT_NAME = ".env"


def env_file_path() -> Path:
    """The file :func:`getenv` falls back to."""
    return Path(os.environ.get(ENV_FILE_VAR) or _DEFAULT_NAME).expanduser()


def read_env_file(path: str | Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines.

    Blank lines and ``#`` comments are skipped, a leading ``export`` is
    allowed so the same file can be ``source``-d, and one layer of matching
    single or double quotes is stripped. Nothing is expanded.
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError):
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def getenv(name: str) -> str | None:
    """Return ``name`` from the environment, else from the ``.env`` file."""
    value = os.environ.get(name)
    if value:
        return value
    return read_env_file(env_file_path()).get(name) or None
