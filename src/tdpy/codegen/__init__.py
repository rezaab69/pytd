"""Generate tdpy's typed API from TDLib's ``td_api.tl``.

Run ``python -m tdpy.codegen`` to regenerate, or ``--check`` to fail when the
committed output no longer matches the schema.
"""

from __future__ import annotations

from tdpy.codegen.emit import emit_functions, emit_methods, emit_types, schema_digest
from tdpy.codegen.tl_parser import Definition, Field, Schema, parse, parse_file

__all__ = [
    "Definition",
    "Field",
    "Schema",
    "emit_functions",
    "emit_methods",
    "emit_types",
    "parse",
    "parse_file",
    "schema_digest",
]
