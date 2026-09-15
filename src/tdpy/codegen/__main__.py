"""``python -m tdpy.codegen`` -- regenerate the typed API, or check it is current."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tdpy.codegen.emit import (
    emit_functions,
    emit_methods,
    emit_types,
    format_source,
    schema_digest,
)
from tdpy.codegen.tl_parser import parse

DEFAULT_SCHEMA = Path("tools/td_api.tl")
DEFAULT_OUTPUT = Path("src/tdpy/api")


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

    rendered = render(args.schema.read_text(encoding="utf-8"))

    if args.check:
        stale = [
            name
            for name, source in rendered.items()
            if not (args.output / name).is_file()
            or (args.output / name).read_text(encoding="utf-8") != source
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
        (args.output / name).write_text(source, encoding="utf-8")
        print(f"wrote {args.output / name} ({source.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
