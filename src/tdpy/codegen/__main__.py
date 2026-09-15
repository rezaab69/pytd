"""``python -m tdpy.codegen`` -- regenerate the typed API, or check it is current."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tdpy.codegen.emit import (
    FormatterUnavailableError,
    emit_functions,
    emit_methods,
    emit_types,
    format_source,
    schema_digest,
)
from tdpy.codegen.tl_parser import parse

DEFAULT_SCHEMA = Path("tools/td_api.tl")
DEFAULT_OUTPUT = Path("src/tdpy/api")


def _normalised(text: str) -> str:
    """Compare on content, not on newlines.

    Generated code is written LF-only, but a checkout can still arrive with
    CRLF. Without this, `--check` fails on the wrong platform instead of on a
    real schema change.
    """
    return text.replace(chr(13) + chr(10), chr(10))


def render(schema_text: str) -> dict[str, str]:
    """Produce every generated module, keyed by filename."""
    schema = parse(schema_text)
    digest = schema_digest(schema_text)
    return {
        "types.py": format_source(emit_types(schema, digest)),
        "functions.py": format_source(emit_functions(schema, digest)),
        "methods.py": format_source(emit_methods(schema, digest)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tdpy.codegen", description=__doc__)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed code differs from the schema",
    )
    args = parser.parse_args(argv)

    if not args.schema.is_file():
        print(f"schema not found: {args.schema}", file=sys.stderr)
        return 2

    try:
        rendered = render(args.schema.read_text(encoding="utf-8"))
    except FormatterUnavailableError as exc:
        # A traceback here would suggest a bug in tdpy rather than a
        # missing tool the caller can install.
        print(exc, file=sys.stderr)
        return 2

    if args.check:
        stale = [
            name
            for name, source in rendered.items()
            if not (args.output / name).is_file()
            or _normalised((args.output / name).read_text(encoding="utf-8")) != _normalised(source)
        ]
        if stale:
            print(
                "Generated API is out of date with " + str(args.schema) + ":",
                *(f"  - {args.output / name}" for name in stale),
                "Run `python -m tdpy.codegen` and commit the result.",
                sep="\n",
                file=sys.stderr,
            )
            return 1
        print(f"Generated API matches {args.schema}.")
        return 0

    args.output.mkdir(parents=True, exist_ok=True)
    for name, source in rendered.items():
        # newline="" keeps Python from translating to the platform ending, so
        # the same schema renders byte-identically on every OS.
        (args.output / name).write_text(source, encoding="utf-8", newline="")
        print(f"wrote {args.output / name} ({source.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
