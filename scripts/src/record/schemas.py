"""
JSON schemas: what the classify and parse steps send to Ollama as `format`,
and the stored record's schema for anything outside Python (the eval
harness, answer keys, reviewers).

The schemas are also committed under scripts/schemas/; a test fails when
they drift from the models. Regenerate them from scripts/src:

    python -m record.schemas
"""

import copy
import json
from pathlib import Path

from record.common import SCHEMA_VERSION
from record.document import DocumentRecord
from record.printed import PARSE_MODELS, ClassifierOutput, DocumentType

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


def inline_refs(schema: dict) -> dict:
    """
    Replace every local "$ref" with the definition it names and drop
    "$defs", so the schema is one self-contained tree. Grammar-constrained
    decoding handles that most reliably.
    """
    defs = schema.get("$defs", {})

    def resolve(node, seen: tuple[str, ...]):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref is not None:
                name = ref.removeprefix("#/$defs/")
                if name in seen:
                    raise ValueError(f"recursive schema at {name}")
                if name not in defs:
                    raise ValueError(f"unknown reference {ref}")
                siblings = {k: v for k, v in node.items() if k != "$ref"}
                return {**resolve(defs[name], seen + (name,)), **siblings}
            return {k: resolve(v, seen) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v, seen) for v in node]
        return node

    return resolve(copy.deepcopy(schema), ())


def strip_titles(node):
    """
    Drop pydantic's generated "title" keywords. They repeat the field names
    and only cost context; descriptions are kept. A property that is itself
    called "title" is left alone.
    """
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "properties":
                out[key] = {name: strip_titles(sub) for name, sub in value.items()}
            elif key == "title" and isinstance(value, str):
                continue
            else:
                out[key] = strip_titles(value)
        return out
    if isinstance(node, list):
        return [strip_titles(v) for v in node]
    return node


def model_format(model) -> dict:
    """A pydantic model's schema as sent to Ollama: inlined, without titles."""
    return strip_titles(inline_refs(model.model_json_schema()))


def classifier_schema() -> dict:
    return model_format(ClassifierOutput)


def parse_output_schema(document_type: DocumentType | str) -> dict:
    """The parse step's output schema for one document type."""
    document_type = DocumentType(document_type)
    model = PARSE_MODELS.get(document_type)
    if model is None:
        raise ValueError(f"documents of type {document_type.value} are not parsed")
    return model_format(model)


def record_schema() -> dict:
    """The stored record's schema, with shared definitions kept in $defs."""
    return DocumentRecord.model_json_schema()


def schema_files() -> dict[str, dict]:
    """File name -> schema, for everything committed under scripts/schemas/."""
    v = SCHEMA_VERSION
    files = {
        f"classify.v{v}.json": classifier_schema(),
        f"document_record.v{v}.json": record_schema(),
    }
    for document_type in PARSE_MODELS:
        files[f"parse_{document_type.value}.v{v}.json"] = parse_output_schema(
            document_type
        )
    return files


def dump(schema: dict) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    SCHEMA_DIR.mkdir(exist_ok=True)
    for name, schema in schema_files().items():
        (SCHEMA_DIR / name).write_text(dump(schema), encoding="utf-8", newline="\n")
        print(f"wrote {SCHEMA_DIR / name}")


if __name__ == "__main__":
    main()
