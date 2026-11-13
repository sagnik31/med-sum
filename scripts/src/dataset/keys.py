"""
Answer keys: the record each synthetic document should produce, in the
stored record format (record.document.DocumentRecord), without provenance.

Citations point at the expected transcription. Line numbers are only
meaningful against that transcription; a harness compares citations by
their quoted text. See docs/adr/0009-measured-evals.md.
"""

from dataset.content import ANALYTES, Doc
from dataset.layout import (
    AUTO,
    Allergy,
    Condition,
    HeaderField,
    LabTest,
    Medicine,
    Remark,
    Vital,
    page_markdown,
)
from record.document import DocumentRecord

# Which header date the document date comes from, in order: the report date,
# else the collection date, else the prescription date. See docs/adr/0003-printed-and-derived-values.md.
DATE_RULES = ("report_date", "collection_date", "prescription_date")


def transcription(doc: Doc) -> list[list[tuple[str, list]]]:
    """The expected transcription: per page, (line, items) pairs."""
    return [page_markdown(blocks) for blocks in doc.page_blocks()]


def transcription_markdown(doc: Doc) -> str:
    """The expected transcription as one Markdown file, for people to read."""
    parts = []
    for n, lines in enumerate(transcription(doc), start=1):
        parts.append(f"<!-- page {n} -->\n" + "\n".join(text for text, _ in lines))
    return "\n\n".join(parts) + "\n"


def build_key(doc: Doc) -> DocumentRecord:
    pages = transcription(doc)
    patient: dict = {k: None for k in ("name", "age", "sex", "date_of_birth", "patient_id")}  # fmt: skip
    details: dict = {
        "facility": None,
        "clinicians": [],
        "report_date": None,
        "collection_date": None,
        "prescription_date": None,
    }
    dates: dict = {}
    groups: dict[str, list] = {k: [] for k in
                               ("observations", "medications", "conditions", "remarks",
                                "vitals", "allergies")}  # fmt: skip

    for page_no, lines in enumerate(pages, start=1):
        for line_no, (text, items) in enumerate(lines, start=1):
            cite = {"page": page_no, "first_line": line_no, "last_line": line_no,
                    "quote": text}  # fmt: skip
            for item in items:
                if isinstance(item, HeaderField):
                    _header(item, cite, patient, details, dates)
                else:
                    group, prefix, printed, derived = _item(item, cite)
                    entry = {
                        "id": f"{prefix}-{len(groups[group]) + 1}",
                        "printed": printed,
                    }
                    if derived is not None:
                        entry["derived"] = derived
                    groups[group].append(entry)

    _link_test_remarks(groups)
    if doc.type == "other":
        assert not any(groups.values()), f"{doc.id}: an 'other' document is not parsed"

    record = {
        "document": {
            "document_id": doc.id,
            "type": doc.type,
            "page_count": len(pages),
            "has_text_layer": doc.file_format == "pdf",
            "printed": details,
            "derived": _document_dates(dates),
        },
        "pages": [
            {"page": n, "lines": [text for text, _ in lines]}
            for n, lines in enumerate(pages, start=1)
        ],
        **groups,
    }
    if doc.type != "other":
        record["patient"] = {"printed": patient}
    return DocumentRecord.model_validate(record)


def _header(item: HeaderField, cite: dict, patient: dict, details: dict, dates: dict):
    group, name = item.path.split(".")
    value = {"citation": cite, "text": item.value}
    target = patient if group == "patient" else details
    if name == "clinicians":
        target[name].append(value)
        return
    if target[name] is not None:
        raise ValueError(f"{item.path} is printed twice")
    target[name] = value
    if item.on is not None:
        dates[name] = item.on


def _document_dates(dates: dict) -> dict:
    derived = {k: dates[k].isoformat() for k in DATE_RULES if k in dates}
    for rule in DATE_RULES:
        if rule in dates:
            return {
                **derived,
                "document_date": dates[rule].isoformat(),
                "date_source": rule,
            }
    return {**derived, "date_source": "undated"}


def _item(item, cite: dict) -> tuple[str, str, dict, dict | None]:
    """(group, id prefix, printed, derived) for one cited item."""
    if isinstance(item, LabTest):
        analyte = ANALYTES.get(item.name) if item.analyte is AUTO else item.analyte
        printed = {
            "citation": cite,
            "panel": item.panel,
            "test_name": item.name,
            "value_text": item.value,
            "unit": item.unit,
            "reference_ranges": [r.text for r in item.ranges],
            "flag_printed": item.flag,
        }
        derived = {
            "analyte": analyte,
            "value_num": item.value_num(),
            "ranges": [
                {"text": r.text, "low": r.low, "high": r.high, "label": r.label}
                for r in item.ranges
            ],
            "flag_computed": item.flag_computed(),
            "converted": item.converted,
        }
        return "observations", "obs", printed, derived
    if isinstance(item, Medicine):
        printed = {
            "citation": cite,
            "form": item.form,
            "name": item.name,
            "strength": item.strength,
            "dose": item.dose,
            "frequency": item.frequency,
            "route": item.route,
            "duration": item.duration,
            "instructions": item.instructions,
        }
        derived = {"schedule": item.schedule, "duration_days": item.duration_days}
        return "medications", "med", printed, derived
    if isinstance(item, Condition):
        printed = {"citation": cite, "text": item.text, "kind": item.kind}
        return "conditions", "cond", printed, None
    if isinstance(item, Remark):
        printed = {
            "citation": cite,
            "text": item.text,
            "kind": item.kind,
            "scope": item.scope,
            "test_name": item.test_name,
            "panel": item.panel,
        }
        return "remarks", "rem", printed, {"observation_id": None}
    if isinstance(item, Vital):
        printed = {"citation": cite, "name": item.name, "value_text": item.value,
                   "unit": item.unit}  # fmt: skip
        derived = {"kind": item.kind, "value_num": item.num, "systolic": item.systolic,
                   "diastolic": item.diastolic}  # fmt: skip
        return "vitals", "vit", printed, derived
    if isinstance(item, Allergy):
        printed = {"citation": cite, "text": item.text, "substance": item.substance,
                   "reaction": item.reaction}  # fmt: skip
        derived = {"no_known_allergies": item.no_known_allergies}
        return "allergies", "alg", printed, derived
    raise TypeError(f"cannot cite {item!r}")


def _link_test_remarks(groups: dict) -> None:
    """Point each test remark at the observation it is printed under."""
    for remark in groups["remarks"]:
        printed = remark["printed"]
        if printed["scope"] != "test":
            continue
        matches = [
            o["id"]
            for o in groups["observations"]
            if o["printed"]["test_name"] == printed["test_name"]
            and o["printed"]["panel"] == printed["panel"]
        ]
        if len(matches) != 1:
            raise ValueError(f"remark {remark['id']} matches observations {matches}")
        remark["derived"]["observation_id"] = matches[0]
