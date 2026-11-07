"""
The synthetic dataset: the committed files match a fresh build, the answer
keys are internally consistent, and each listed case is really there.
"""

import json
import re
import unicodedata

import pymupdf
import pytest

from dataset import build, content
from dataset.layout import LabTest, Range, rng
from dataset.render import render
from record.document import DocumentRecord

DATASET = build.DATASET_DIR
MANIFEST = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
DOCS = {d["id"]: d for d in MANIFEST["documents"]}


@pytest.fixture(scope="module")
def fresh_build() -> dict[str, bytes]:
    return build.outputs()


@pytest.fixture(scope="module")
def keys() -> dict[str, DocumentRecord]:
    return {
        doc_id: DocumentRecord.model_validate_json(
            (DATASET / entry["key"]).read_text(encoding="utf-8")
        )
        for doc_id, entry in DOCS.items()
    }


def normalise(text: str) -> str:
    """Compare text-layer text loosely: ligatures expanded, spacing collapsed."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


# -------------------------------------------------------------------
# The committed files are what the generator writes
# -------------------------------------------------------------------
def test_the_committed_dataset_matches_a_fresh_build(fresh_build):
    committed = {
        p.relative_to(DATASET).as_posix()
        for p in DATASET.rglob("*")
        if p.is_file() and p.name != ".gitattributes"
    }
    assert committed == set(fresh_build), (
        "scripts/evals/dataset/ is out of date: run `python -m dataset.build` "
        "from scripts/src"
    )
    for rel, data in fresh_build.items():
        on_disk = (DATASET / rel).read_bytes()
        if not rel.startswith("documents/"):  # text: git may check out CRLF
            on_disk = on_disk.replace(b"\r\n", b"\n")
        assert on_disk == data, f"{rel} differs from a fresh build"


@pytest.mark.parametrize("doc_id", ["d01", "d03", "d04"])
def test_rendering_is_deterministic(doc_id):
    doc = next(d for d in content.DOCUMENTS if d.id == doc_id)

    assert render(doc) == render(doc)


# -------------------------------------------------------------------
# Size and shape (decisions 7, 8)
# -------------------------------------------------------------------
def test_the_dataset_has_the_agreed_size():
    pages = sum(d["pages"] for d in DOCS.values())

    assert 3 <= len(MANIFEST["patients"]) <= 4
    assert 15 <= len(DOCS) <= 20
    assert 20 <= pages <= 35


def test_every_patient_has_several_documents_across_dates():
    for patient in MANIFEST["patients"]:
        docs = [d for d in DOCS.values() if d["patient"] == patient["key"]]
        dates = {d["document_date"] for d in docs if d["document_date"]}
        assert len(docs) >= 3 and len(dates) >= 3, patient["key"]


def test_upload_order_covers_every_document_once():
    assert sorted(MANIFEST["upload_order"]) == sorted(DOCS)


MAGIC = {
    "application/pdf": b"%PDF-",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}


@pytest.mark.parametrize("doc_id", sorted(DOCS))
def test_each_file_is_the_type_the_backend_expects(doc_id):
    entry = DOCS[doc_id]
    data = (DATASET / entry["file"]).read_bytes()

    assert data.startswith(MAGIC[entry["content_type"]])
    assert len(data) < 20 * 1024 * 1024  # the upload limit


@pytest.mark.parametrize("doc_id", sorted(DOCS))
def test_page_counts_and_text_layers_match_the_manifest(doc_id):
    entry = DOCS[doc_id]
    with pymupdf.open(DATASET / entry["file"]) as doc:
        assert len(doc) == entry["pages"]
        text = "".join(page.get_text() for page in doc)
    assert bool(text.strip()) == entry["has_text_layer"]


# -------------------------------------------------------------------
# The answer keys are internally consistent
# -------------------------------------------------------------------
def cited_items(record: DocumentRecord):
    """(citation, printed strings that must be on the cited line) for every item."""
    patient = record.patient.printed if record.patient else None
    details = record.document.printed
    patient_fields = (
        [getattr(patient, f) for f in type(patient).model_fields] if patient else []
    )
    for value in [
        *patient_fields,
        details.facility,
        *details.clinicians,
        details.report_date,
        details.collection_date,
        details.prescription_date,
    ]:
        if value is not None:
            yield value.citation, [value.text]
    for item in record.items():
        printed = item.printed.model_dump(exclude={"citation", "panel", "test_name"})
        strings = []
        for value in printed.values():
            if isinstance(value, str) and value not in ("advice", "comment", "test",
                                                        "panel", "document"):  # fmt: skip
                strings.append(value)
            elif isinstance(value, list):
                strings += value
        yield item.printed.citation, strings


def test_every_citation_quotes_its_lines_exactly(keys):
    for doc_id, record in keys.items():
        for citation, _ in cited_items(record):
            page = record.pages[citation.page - 1]
            assert (
                page.quote(citation.first_line, citation.last_line) == citation.quote
            ), doc_id


def test_every_printed_value_is_on_its_cited_line(keys):
    # What validator 1 will check on model output, checked here on the keys.
    for doc_id, record in keys.items():
        for citation, strings in cited_items(record):
            for s in strings:
                if s in ("diagnosis", "provisional_diagnosis", "impression", "history"):
                    continue  # condition kinds are labels, not printed text
                assert s in citation.quote, f"{doc_id}: {s!r} not in {citation.quote!r}"


def test_every_panel_is_a_heading_above_its_results(keys):
    for doc_id, record in keys.items():
        for obs in record.observations:
            page = record.pages[obs.printed.citation.page - 1]
            above = page.lines[: obs.printed.citation.first_line - 1]
            headings = [line for line in above if line.startswith("## ")]
            assert headings and headings[-1] == f"## {obs.printed.panel}", doc_id


def test_test_remarks_point_at_their_observations(keys):
    linked = 0
    for record in keys.values():
        for remark in record.remarks:
            if remark.printed.scope.value == "test":
                target = next(
                    o
                    for o in record.observations
                    if o.id == remark.derived.observation_id
                )
                assert target.printed.test_name == remark.printed.test_name
                linked += 1
    assert linked >= 2


@pytest.mark.parametrize(
    "doc_id", [d for d in sorted(DOCS) if DOCS[d]["has_text_layer"]]
)
def test_values_are_in_the_pdf_text_layer(doc_id, keys):
    # Validator 2 (decision 40) compares extracted values with the PDF's text.
    record = keys[doc_id]
    with pymupdf.open(DATASET / DOCS[doc_id]["file"]) as pdf:
        texts = [normalise(page.get_text()) for page in pdf]
    for obs in record.observations:
        page_text = texts[obs.printed.citation.page - 1]
        for value in (obs.printed.test_name, obs.printed.value_text, obs.printed.unit):
            if value:
                assert normalise(value) in page_text, f"{doc_id}: {value!r}"


def test_types_have_the_items_they_should(keys):
    for doc_id, record in keys.items():
        kind = record.document.type.value
        if kind == "lab_report":
            assert record.observations and not record.medications, doc_id
        elif kind == "prescription":
            assert record.medications and not record.observations, doc_id
        else:
            assert list(record.items()) == [] and record.patient is None, doc_id


@pytest.mark.parametrize(
    "doc_id, source",
    [("d01", "report_date"), ("d03", "collection_date"), ("d08", "report_date"),
     ("d02", "prescription_date"), ("d15", "undated"), ("d17", "undated")],
)  # fmt: skip
def test_the_document_date_follows_decision_13(doc_id, source, keys):
    assert keys[doc_id].document.derived.date_source.value == source


# -------------------------------------------------------------------
# The listed cases are really in the data
# -------------------------------------------------------------------
REQUIRED_CASES = {
    "panel_remark_after_last_row",  # bug 1
    "document_remark_after_single_test",  # bug 1
    "single_creatinine",  # bugs 2, 3
    "bun_in_kidney_panel",  # bug 6
    "statin_without_lipid_panel",  # bug 7
    "uploaded_before_older_documents",  # bug 5
    "trend_two_values",  # decision 31
    "multiple_ranges",  # decision 26
    "patient_mismatch",  # question 5
    "undated",  # decision 13
    "classified_other",  # decision 1
    "image_only_pdf",  # decisions 24, 40
}


def test_every_required_case_is_covered():
    covered = {case for d in DOCS.values() for case in d["cases"]}

    assert REQUIRED_CASES <= covered


def observations_of(keys, patient: str):
    return [
        o
        for doc_id, record in keys.items()
        if DOCS[doc_id]["patient"] == patient and doc_id != "d16"
        for o in record.observations
    ]


def test_rohan_has_exactly_one_creatinine(keys):
    values = [
        o for o in observations_of(keys, "rohan") if o.derived.analyte == "creatinine"
    ]

    assert [o.printed.value_text for o in values] == ["1.6"]


def test_bun_is_in_the_kidney_panel(keys):
    bun = [
        o for r in keys.values() for o in r.observations if o.derived.analyte == "bun"
    ]

    assert bun and all(o.printed.panel == "KIDNEY FUNCTION TEST" for o in bun)


def test_rohan_has_a_statin_but_no_lipid_panel(keys):
    meds = [m.printed.name for doc_id, r in keys.items()
            if DOCS[doc_id]["patient"] == "rohan" for m in r.medications]  # fmt: skip
    lipids = {
        "cholesterol_total",
        "ldl_cholesterol",
        "hdl_cholesterol",
        "triglycerides",
    }

    assert "Atorva" in meds
    assert not [
        o for o in observations_of(keys, "rohan") if o.derived.analyte in lipids
    ]


def test_the_panel_remark_sits_right_after_the_last_row(keys):
    record = keys["d01"]
    remark = record.remarks[0]
    last_cbc = [
        o for o in record.observations if o.printed.panel == remark.printed.panel
    ][-1]

    assert remark.printed.scope.value == "panel"
    assert last_cbc.printed.test_name == "Total WBC Count"
    # Only the blank line between blocks separates them.
    assert (
        remark.printed.citation.first_line == last_cbc.printed.citation.first_line + 2
    )


def test_upload_order_is_not_document_order_for_some_patients():
    order = MANIFEST["upload_order"]
    out_of_order = []
    for patient in MANIFEST["patients"]:
        dated = [d for d in order if DOCS[d]["patient"] == patient["key"]
                 and DOCS[d]["document_date"]]  # fmt: skip
        dates = [DOCS[d]["document_date"] for d in dated]
        if dates != sorted(dates):
            out_of_order.append(patient["key"])
    assert len(out_of_order) >= 2


def test_two_values_of_the_same_test_exist_for_trends(keys):
    tsh = [o.derived.value_num for o in observations_of(keys, "fatima")
           if o.derived.analyte == "tsh"]  # fmt: skip

    assert tsh == [8.9, 3.1]


def test_printed_names_match_the_patient_except_the_mismatch(keys):
    for doc_id, record in keys.items():
        if record.patient is None or record.patient.printed.name is None:
            continue
        expected = next(p["name"] for p in MANIFEST["patients"]
                        if p["key"] == DOCS[doc_id]["patient"])  # fmt: skip
        matches = expected in record.patient.printed.name.text
        assert matches == (doc_id != "d16"), doc_id


def test_every_page_says_it_is_synthetic(keys):
    for record in keys.values():
        for page in record.pages:
            assert page.lines[-1] == content.FOOTER


# -------------------------------------------------------------------
# Generator helpers
# -------------------------------------------------------------------
def test_rng_only_accepts_plain_ranges():
    assert rng("12.0 - 15.0") == Range("12.0 - 15.0", 12.0, 15.0)
    for text in ["< 150", "1,50,000 - 4,10,000", "Normal: < 5.7"]:
        with pytest.raises(ValueError):
            rng(text)


@pytest.mark.parametrize(
    "test, num, flag",
    [
        (LabTest("Hb", "10.4", "g/dL", (rng("12.0 - 15.0"),)), 10.4, "low"),
        (LabTest("Hb", "12.0", "g/dL", (rng("12.0 - 15.0"),)), 12.0, "normal"),
        (LabTest("Hb", "15.1", "g/dL", (rng("12.0 - 15.0"),)), 15.1, "high"),
        (LabTest("TG", "168", "mg/dL", (Range("< 150", high=150),)), 168.0, "high"),
        (LabTest("HDL", "44", "mg/dL", (Range("> 50", low=50),)), 44.0, "low"),
        (LabTest("CRP", "< 0.5", "mg/L", (Range("< 5.0", high=5.0),)), None, None),
        (LabTest("Plt", "2,45,000", "/cumm", (Range("x", 150000, 410000),), num=245000),
         245000, "normal"),
        (LabTest("A1c", "8.1", "%", content.HBA1C_RANGES), 8.1, None),
        (LabTest("Prot", "Nil", None, (Range("Nil"),)), None, None),
    ],
)  # fmt: skip
def test_expected_derived_values(test, num, flag):
    assert test.value_num() == num
    assert test.flag_computed() == flag
