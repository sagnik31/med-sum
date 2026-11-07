"""JSON schema export: what Ollama is given, and the committed schema files."""

import json

import pytest

from record.schemas import (
    SCHEMA_DIR,
    classifier_schema,
    inline_refs,
    parse_output_schema,
    record_schema,
    schema_files,
    strip_titles,
)

PARSE_TYPES = ["lab_report", "prescription"]

# Values that code computes; the model is never asked for them (decisions 10-13).
DERIVED_FIELDS = {
    "value_num",
    "flag_computed",
    "analyte",
    "converted",
    "ranges",
    "schedule",
    "duration_days",
    "document_date",
    "date_source",
    "no_known_allergies",
    "observation_id",
}
# Never part of the MVP's parse schemas: placeholders, provenance, review.
NOT_PARSED = {"procedures", "encounters", "imaging_findings", "provenance",
              "corrections", "status", "reviewed_by"}  # fmt: skip


def objects(node):
    """Every object schema in a schema tree."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from objects(value)


def property_names(schema) -> set[str]:
    return {name for obj in objects(schema) for name in obj.get("properties", {})}


@pytest.fixture(params=PARSE_TYPES)
def parse_schema(request):
    return parse_output_schema(request.param)


def test_parse_schemas_are_self_contained(parse_schema):
    text = json.dumps(parse_schema)

    assert "$ref" not in text
    assert "$defs" not in text


def test_parse_schemas_close_every_object_and_require_every_field(parse_schema):
    for obj in objects(parse_schema):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


def test_parse_schemas_never_ask_the_model_for_derived_values(parse_schema):
    assert property_names(parse_schema) & (DERIVED_FIELDS | NOT_PARSED) == set()


def test_parse_schemas_keep_descriptions_but_drop_titles(parse_schema):
    text = json.dumps(parse_schema)

    assert '"title"' not in text
    assert "The result exactly as printed." in text or "exactly as written" in text


def test_each_type_asks_for_its_own_items():
    lab = property_names(parse_output_schema("lab_report"))
    rx = property_names(parse_output_schema("prescription"))

    assert {"results", "reference_ranges", "flag_printed"} <= lab - rx
    assert {"medicines", "frequency", "strength"} <= rx - lab
    assert {"patient", "details", "vitals", "allergies", "remarks"} <= lab & rx


@pytest.mark.parametrize("label", ["other", "discharge_summary"])
def test_types_that_are_not_parsed_have_no_schema(label):
    with pytest.raises(ValueError, match="not parsed"):
        parse_output_schema(label)


def test_an_unknown_type_is_refused():
    with pytest.raises(ValueError):
        parse_output_schema("x_ray")


def test_the_classifier_schema_is_the_label_list():
    assert classifier_schema() == {
        "additionalProperties": False,
        "properties": {
            "document_type": {
                "enum": ["lab_report", "prescription", "other"],
                "type": "string",
            }
        },
        "required": ["document_type"],
        "type": "object",
    }


def test_the_record_schema_includes_derived_values_and_placeholders():
    names = property_names(inline_refs(record_schema()))

    assert DERIVED_FIELDS <= names
    assert {"procedures", "encounters", "imaging_findings"} <= names
    assert record_schema()["properties"]["schema_version"]["const"] == 1


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def test_inline_refs_replaces_references_and_keeps_siblings():
    schema = {
        "$defs": {"Name": {"type": "string", "minLength": 1}},
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/Name", "description": "A name."}},
    }

    assert inline_refs(schema) == {
        "type": "object",
        "properties": {
            "a": {"type": "string", "minLength": 1, "description": "A name."}
        },
    }


def test_inline_refs_refuses_recursive_and_unknown_references():
    recursive = {
        "$defs": {"Node": {"properties": {"next": {"$ref": "#/$defs/Node"}}}},
        "$ref": "#/$defs/Node",
    }
    with pytest.raises(ValueError, match="recursive"):
        inline_refs(recursive)
    with pytest.raises(ValueError, match="unknown reference"):
        inline_refs({"$ref": "#/$defs/Missing"})


def test_strip_titles_keeps_a_property_called_title():
    schema = {
        "title": "Doc",
        "properties": {"title": {"title": "Title", "type": "string"}},
    }

    assert strip_titles(schema) == {"properties": {"title": {"type": "string"}}}


# -------------------------------------------------------------------
# Committed files
# -------------------------------------------------------------------
def test_the_committed_schema_files_are_up_to_date():
    expected = schema_files()
    committed = {p.name for p in SCHEMA_DIR.glob("*.json")}

    assert committed == set(expected), (
        "scripts/schemas/ is out of date: run `python -m record.schemas` "
        "from scripts/src"
    )
    for name, schema in expected.items():
        on_disk = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
        assert on_disk == schema, (
            f"{name} is out of date: run `python -m record.schemas` from scripts/src"
        )
