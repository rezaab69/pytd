"""Parse TDLib's ``td_api.tl`` into a structured schema.

The grammar is small but the doc comments are not. TDLib attaches everything to
``//@`` tags whose names are the field names of the declaration that follows,
so the comment block can only be split once the declaration has been read --
which is why docs are buffered and resolved after the fact rather than during.

Continuation lines start with ``//-`` and belong to the preceding tag.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Definition",
    "Field",
    "Schema",
    "parse",
    "parse_file",
]

#: Declarations for these result types are TL boilerplate, not part of the API.
TL_PRIMITIVE_RESULTS = frozenset(
    {"Double", "String", "Int32", "Int53", "Int64", "Bytes", "Bool", "Vector"}
)

#: Field types that map onto Python builtins rather than generated classes.
TL_PRIMITIVE_TYPES = frozenset({"double", "string", "int32", "int53", "int64", "bytes", "Bool"})

_DECLARATION = re.compile(
    r"^(?P<name>[a-z][A-Za-z0-9_]*)"
    r"(?P<params>(?: [A-Za-z0-9_]+:[A-Za-z0-9_<>]+)*)"
    r" = (?P<result>[A-Z][A-Za-z0-9_]*);$"
)
_CLASS = re.compile(r"^//@class (?P<name>[A-Z][A-Za-z0-9_]*)(?: @description (?P<doc>.*))?$")
_FUNCTIONS_MARKER = "---functions---"
_SYNCHRONOUS_MARKER = "Can be called synchronously"
# Types say "may be null"; functions say "pass null if none". Both mean the
# field is optional, and TDLib never applies either to a primitive.
_NULLABLE_PATTERN = re.compile(r"may be null|pass null", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Field:
    """One parameter of a constructor or function."""

    name: str
    tl_type: str
    doc: str = ""
    nullable: bool = False

    @property
    def is_primitive(self) -> bool:
        return self.tl_type in TL_PRIMITIVE_TYPES


@dataclass(frozen=True, slots=True)
class Definition:
    """A constructor (a data type) or a function (a callable method)."""

    name: str
    result: str
    fields: tuple[Field, ...] = ()
    doc: str = ""
    is_function: bool = False
    is_synchronous: bool = False

    @property
    def class_name(self) -> str:
        """The generated Python class name: ``sendMessage`` -> ``SendMessage``."""
        return self.name[0].upper() + self.name[1:]


@dataclass(frozen=True, slots=True)
class Schema:
    """Everything ``td_api.tl`` declares."""

    classes: dict[str, str]
    constructors: tuple[Definition, ...]
    functions: tuple[Definition, ...]

    @property
    def members(self) -> dict[str, tuple[str, ...]]:
        """Abstract class name -> the constructors that implement it."""
        grouped: dict[str, list[str]] = {name: [] for name in self.classes}
        for definition in self.constructors:
            if definition.result in grouped:
                grouped[definition.result].append(definition.name)
        return {name: tuple(members) for name, members in grouped.items()}

    def is_abstract(self, tl_type: str) -> bool:
        """Whether ``tl_type`` names a union rather than a single constructor."""
        return tl_type in self.classes


def _split_doc(block: str, field_names: tuple[str, ...]) -> tuple[str, dict[str, str]]:
    """Split a comment block into its description and per-field docs.

    The tags are ``@description`` plus one per field, and descriptions contain
    ``@`` freely (handles, emails, "@durov"), so only the declaration's own
    field names are treated as tags. Anything else is prose.
    """
    if not block:
        return "", {}

    keys = ["description", *field_names]
    # Longest first so @id cannot claim the start of @id_type.
    alternatives = "|".join(re.escape(key) for key in sorted(keys, key=len, reverse=True))
    pattern = re.compile(rf"(?:^|(?<=\s))@({alternatives})(?=\s|$)")

    parts = pattern.split(block)
    description = ""
    docs: dict[str, str] = {}
    for key, text in zip(parts[1::2], parts[2::2], strict=False):
        value = text.strip()
        if key == "description":
            description = value
        else:
            docs[key] = value
    return description, docs


def _parse_fields(params: str, docs: dict[str, str]) -> tuple[Field, ...]:
    fields = []
    for param in params.split():
        name, _, tl_type = param.partition(":")
        doc = docs.get(name, "")
        fields.append(
            Field(
                name=name,
                tl_type=tl_type,
                doc=doc,
                # TDLib only ever says so in prose; there is no schema marker.
                nullable=bool(_NULLABLE_PATTERN.search(doc)),
            )
        )
    return tuple(fields)


def parse(text: str) -> Schema:
    """Parse the contents of ``td_api.tl``.

    Raises:
        ValueError: if the functions separator is missing, which would silently
            turn every function into a data type.
    """
    lines = text.split("\n")
    if _FUNCTIONS_MARKER not in lines:
        raise ValueError(f"schema has no {_FUNCTIONS_MARKER!r} separator")

    classes: dict[str, str] = {}
    constructors: list[Definition] = []
    functions: list[Definition] = []
    buffer: list[str] = []
    in_functions = False

    for line in lines:
        if line == _FUNCTIONS_MARKER:
            in_functions = True
            buffer.clear()
            continue

        if not line.strip():
            buffer.clear()
            continue

        class_match = _CLASS.match(line)
        if class_match is not None:
            classes[class_match["name"]] = (class_match["doc"] or "").strip()
            buffer.clear()
            continue

        if line.startswith("//-"):
            # A continuation of the previous tag, broken only for line length.
            if buffer:
                buffer[-1] = f"{buffer[-1]} {line[3:]}"
            continue

        if line.startswith("//"):
            buffer.append(line[2:])
            continue

        match = _DECLARATION.match(line)
        if match is None:
            buffer.clear()
            continue
        if match["result"] in TL_PRIMITIVE_RESULTS:
            # The primitive prelude: `int32 = Int32;` and friends.
            buffer.clear()
            continue

        block = " ".join(buffer).strip()
        buffer.clear()
        params = match["params"].strip()
        field_names = tuple(param.partition(":")[0] for param in params.split())
        description, docs = _split_doc(block, field_names)

        definition = Definition(
            name=match["name"],
            result=match["result"],
            fields=_parse_fields(params, docs),
            doc=description,
            is_function=in_functions,
            is_synchronous=_SYNCHRONOUS_MARKER in description,
        )
        (functions if in_functions else constructors).append(definition)

    return Schema(classes=classes, constructors=tuple(constructors), functions=tuple(functions))


def parse_file(path: str | Path) -> Schema:
    """Parse a ``td_api.tl`` file from disk."""
    return parse(Path(path).read_text(encoding="utf-8"))
