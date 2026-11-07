"""
Example records for the schema tests: a fictional one-page CBC report, as
the parse step would return it and as it is stored.
"""

from record.common import Citation, PrintedText, Provenance

CBC_LINES = [
    "# Sunrise Diagnostics, Pune",
    "Patient: Asha Rao | Age/Sex: 52 Y / F | UHID: SD-00417",
    "Ref. by: Dr. K. Mehta",
    "Collected: 03/03/2025 08:10 | Reported: 03/03/2025 14:30",
    "## COMPLETE BLOOD COUNT",
    "| Test | Result | Unit | Reference Range |",
    "| --- | --- | --- | --- |",
    "| Haemoglobin | 10.4 L | g/dL | 12.0 - 15.0 |",
    "| Platelet Count | 1.5 | lakh/cumm | 1.5 - 4.5 |",
    "Remark: Microcytic hypochromic picture.",
    "Advice: Repeat CBC after 4 weeks.",
    "Allergies: NKDA",
]


def cite(line: int, last: int | None = None) -> dict:
    last = last or line
    return {
        "page": 1,
        "first_line": line,
        "last_line": last,
        "quote": "\n".join(CBC_LINES[line - 1 : last]),
    }


def printed(text: str, line: int) -> dict:
    return {"citation": cite(line), "text": text}


def cbc_parse() -> dict:
    """The parse step's output for the CBC page (LabReportParse)."""
    return {
        "patient": {
            "name": printed("Asha Rao", 2),
            "age": printed("52 Y", 2),
            "sex": printed("F", 2),
            "date_of_birth": None,
            "patient_id": printed("SD-00417", 2),
        },
        "details": {
            "facility": printed("Sunrise Diagnostics, Pune", 1),
            "clinicians": [printed("Ref. by: Dr. K. Mehta", 3)],
            "report_date": printed("03/03/2025 14:30", 4),
            "collection_date": printed("03/03/2025 08:10", 4),
            "prescription_date": None,
        },
        "results": [
            {
                "citation": cite(8),
                "panel": "COMPLETE BLOOD COUNT",
                "test_name": "Haemoglobin",
                "value_text": "10.4",
                "unit": "g/dL",
                "reference_ranges": ["12.0 - 15.0"],
                "flag_printed": "L",
            },
            {
                "citation": cite(9),
                "panel": "COMPLETE BLOOD COUNT",
                "test_name": "Platelet Count",
                "value_text": "1.5",
                "unit": "lakh/cumm",
                "reference_ranges": ["1.5 - 4.5"],
                "flag_printed": None,
            },
        ],
        "remarks": [
            {
                "citation": cite(10),
                "text": "Microcytic hypochromic picture.",
                "kind": "comment",
                "scope": "panel",
                "test_name": None,
                "panel": "COMPLETE BLOOD COUNT",
            },
            {
                "citation": cite(11),
                "text": "Repeat CBC after 4 weeks.",
                "kind": "advice",
                "scope": "document",
                "test_name": None,
                "panel": None,
            },
        ],
        "conditions": [],
        "vitals": [],
        "allergies": [
            {
                "citation": cite(12),
                "text": "NKDA",
                "substance": None,
                "reaction": None,
            }
        ],
    }


def provenance(**overrides) -> dict:
    return {
        "run_id": "run-1",
        "node": "parse",
        "step_id": "step-3",
        "model": "qwen3.5:4b",
        "prompt_name": "parse_lab_report",
        "prompt_hash": "sha256:0f1e",
        **overrides,
    }


def cbc_record() -> dict:
    """The stored record for the CBC page (DocumentRecord), as JSON data."""
    parsed = cbc_parse()
    hb, platelets = parsed["results"]
    comment, advice = parsed["remarks"]
    return {
        "schema_version": 1,
        "document": {
            "document_id": "doc-1",
            "type": "lab_report",
            "page_count": 1,
            "has_text_layer": True,
            "uploaded_by": None,
            "printed": parsed["details"],
            "derived": {
                "report_date": "2025-03-03",
                "collection_date": "2025-03-03",
                "prescription_date": None,
                "document_date": "2025-03-03",
                "date_source": "report_date",
            },
            "provenance": provenance(),
        },
        "pages": [{"page": 1, "lines": CBC_LINES, "text_layer": "\n".join(CBC_LINES)}],
        "patient": {"printed": parsed["patient"], "provenance": provenance()},
        "observations": [
            {
                "id": "obs-1",
                "printed": hb,
                "derived": {
                    "analyte": "haemoglobin",
                    "value_num": 10.4,
                    "ranges": [{"text": "12.0 - 15.0", "low": 12.0, "high": 15.0}],
                    "flag_computed": "low",
                },
                "provenance": provenance(status="validated"),
            },
            {
                "id": "obs-2",
                "printed": platelets,
                "derived": {
                    "analyte": "platelet_count",
                    "value_num": 1.5,
                    "ranges": [{"text": "1.5 - 4.5", "low": 1.5, "high": 4.5}],
                    "flag_computed": "normal",
                    "converted": {
                        "value": 150000,
                        "unit": "/cumm",
                        "from_unit": "lakh/cumm",
                    },
                },
                "provenance": provenance(status="validated"),
            },
        ],
        "remarks": [
            {"id": "rem-1", "printed": comment},
            {"id": "rem-2", "printed": advice},
        ],
        "allergies": [
            {
                "id": "alg-1",
                "printed": parsed["allergies"][0],
                "derived": {"no_known_allergies": True},
            }
        ],
    }


# Typed helpers for tests that build single parts.
def a_citation(**overrides) -> Citation:
    return Citation(**{**cite(8), **overrides})


def a_printed_text(text="Asha Rao") -> PrintedText:
    return PrintedText(citation=a_citation(), text=text)


def a_provenance(**overrides) -> Provenance:
    return Provenance(**provenance(**overrides))
