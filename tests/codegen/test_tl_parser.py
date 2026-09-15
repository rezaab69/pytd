from __future__ import annotations

import pytest

from tdpy.codegen.tl_parser import parse, parse_file

MINIMAL = """double ? = Double;
int32 = Int32;
bytes = Bytes;
boolTrue = Bool;

//@description A thing @id Its identifier @name Its name; may be null
thing id:int53 name:string = Thing;

//@class Shape @description Something with a shape

//@description A round shape @radius How round
shapeCircle radius:double = Shape;

//@description A square shape
shapeSquare = Shape;

---functions---

//@description Fetches a thing. Can be called synchronously @thing_id Which one
getThing thing_id:int53 = Thing;
"""


@pytest.fixture(scope="module")
def schema():
    return parse(MINIMAL)


def test_primitive_prelude_is_not_part_of_the_api(schema) -> None:
    names = {d.name for d in schema.constructors}
    assert names == {"thing", "shapeCircle", "shapeSquare"}
    assert "int32" not in names
    assert "boolTrue" not in names


def test_constructors_and_functions_are_separated(schema) -> None:
    assert [d.name for d in schema.functions] == ["getThing"]
    assert all(not d.is_function for d in schema.constructors)
    assert all(d.is_function for d in schema.functions)


def test_fields_keep_their_tl_type_verbatim(schema) -> None:
    thing = next(d for d in schema.constructors if d.name == "thing")
    assert [(f.name, f.tl_type) for f in thing.fields] == [("id", "int53"), ("name", "string")]


def test_descriptions_and_field_docs_are_separated(schema) -> None:
    thing = next(d for d in schema.constructors if d.name == "thing")
    assert thing.doc == "A thing"
    assert thing.fields[0].doc == "Its identifier"


def test_nullable_is_taken_from_the_prose(schema) -> None:
    thing = next(d for d in schema.constructors if d.name == "thing")
    assert not thing.fields[0].nullable
    assert thing.fields[1].nullable, "TDLib marks optionality only in prose"


def test_synchronous_methods_are_flagged(schema) -> None:
    assert next(d for d in schema.functions if d.name == "getThing").is_synchronous


def test_abstract_classes_are_collected(schema) -> None:
    assert schema.classes == {"Shape": "Something with a shape"}
    assert schema.is_abstract("Shape")
    assert not schema.is_abstract("Thing")


def test_union_members_are_grouped_by_result(schema) -> None:
    assert schema.members == {"Shape": ("shapeCircle", "shapeSquare")}


def test_a_constructor_with_no_fields_is_allowed(schema) -> None:
    assert next(d for d in schema.constructors if d.name == "shapeSquare").fields == ()


def test_class_name_is_the_pascal_case_constructor(schema) -> None:
    assert next(d for d in schema.constructors if d.name == "shapeCircle").class_name == (
        "ShapeCircle"
    )


def test_missing_functions_marker_is_an_error() -> None:
    # Without it every function would silently become a data type.
    with pytest.raises(ValueError, match="functions"):
        parse("//@description x\nthing id:int53 = Thing;\n")


def test_continuation_lines_rejoin_the_previous_tag() -> None:
    schema = parse(
        "//@description A thing @note Starts here,\n"
        "//-and finishes on the next line\n"
        "thing note:string = Thing;\n"
        "---functions---\n"
    )
    note = schema.constructors[0].fields[0]

    assert note.doc == "Starts here, and finishes on the next line"


def test_at_signs_in_prose_are_not_treated_as_tags() -> None:
    # Only the declaration's own field names are tags; @durov is just text.
    schema = parse(
        "//@description Mentions @durov and e@mail @text The text\n"
        "thing text:string = Thing;\n"
        "---functions---\n"
    )
    thing = schema.constructors[0]

    assert thing.doc == "Mentions @durov and e@mail"
    assert thing.fields[0].doc == "The text"


def test_a_blank_line_discards_an_orphan_comment() -> None:
    schema = parse(
        "//@description Belongs to nothing\n"
        "\n"
        "//@description The real one\n"
        "thing = Thing;\n"
        "---functions---\n"
    )

    assert schema.constructors[0].doc == "The real one"


def test_nested_vector_types_survive_intact() -> None:
    schema = parse(
        "//@description A grid @rows The rows\n"
        "grid rows:vector<vector<cell>> = Grid;\n"
        "---functions---\n"
    )

    assert schema.constructors[0].fields[0].tl_type == "vector<vector<cell>>"


def test_undocumented_fields_get_an_empty_doc() -> None:
    schema = parse("//@description A thing\nthing id:int53 = Thing;\n---functions---\n")

    assert schema.constructors[0].fields[0].doc == ""
    assert not schema.constructors[0].fields[0].nullable


def test_the_real_schema_parses_to_the_expected_shape() -> None:
    schema = parse_file("tools/td_api.tl")

    assert len(schema.constructors) == 2183
    assert len(schema.functions) == 1022
    assert len(schema.classes) == 211
    # TDLib documents exactly these as safe to run off the request queue.
    assert sum(f.is_synchronous for f in schema.functions) == 28
    assert all(d.doc for d in schema.constructors), "every definition is documented"


def test_every_field_type_resolves_to_something_generatable() -> None:
    from tdpy.codegen.tl_parser import TL_PRIMITIVE_TYPES

    schema = parse_file("tools/td_api.tl")
    known = {d.name for d in schema.constructors} | set(schema.classes) | TL_PRIMITIVE_TYPES

    unknown = set()
    for definition in schema.constructors + schema.functions:
        for field in definition.fields:
            inner = field.tl_type
            while inner.startswith("vector<"):
                inner = inner[len("vector<") : -1]
            if inner not in known:
                unknown.add(inner)

    assert unknown == set(), f"field types with no definition: {sorted(unknown)}"


OPTIONALS = """//@description A thing
thing = Thing;
---functions---
//@description Sends @markup Markup; pass null if none @body The body
send markup:ReplyMarkup body:string = Thing;
"""


def test_pass_null_marks_function_parameters_optional() -> None:
    # Functions never say "may be null" -- they say "pass null if none". Missing
    # that would make reply_markup a required argument of sendMessage.
    markup, body = parse(OPTIONALS).functions[0].fields

    assert markup.nullable
    assert not body.nullable


def test_real_schema_optional_fields_are_never_primitive() -> None:
    from tdpy.codegen.tl_parser import TL_PRIMITIVE_TYPES

    schema = parse_file("tools/td_api.tl")
    optional = [
        (d.name, f) for d in schema.constructors + schema.functions for f in d.fields if f.nullable
    ]

    assert len(optional) == 595
    assert not [n for n, f in optional if f.tl_type in TL_PRIMITIVE_TYPES]


def test_send_message_optional_arguments_are_detected() -> None:
    schema = parse_file("tools/td_api.tl")
    send = next(d for d in schema.functions if d.name == "sendMessage")
    optional = {f.name for f in send.fields if f.nullable}

    assert {"reply_markup", "options", "reply_to"} <= optional
    assert "chat_id" not in optional
    assert "input_message_content" not in optional
