"""
Derived values (decisions 10-13, 26): numbers, ranges, computed flags, the
unit table, dose schedules, durations, dates, and the record built from a
parse output. The answer keys, whose derived values were written by hand,
are the oracle.
"""

from datetime import date

import pytest

from dataset import content
from dataset_records import (
    HELD_OUT_IDS,
    MAIN_IDS,
    MAIN_PARSED_IDS,
    PARSED_IDS,
    key,
    parse_of,
)
from derive import build_record
from derive.build import allergy, remark, vital
from derive.dates import document_dates, parse_date
from derive.medicines import parse_duration, parse_frequency
from derive.numbers import compute_flag, parse_number, parse_range, same_unit
from derive.units import (
    ANALYTES,
    UNITS,
    convert,
    convertible,
    lookup_analyte,
    lookup_unit,
)
from record.common import Citation, PrintedText
from record.document import DateSource, Observation, ObservationDerived
from record.printed import (
    PrintedAllergy,
    PrintedDocumentDetails,
    PrintedLabResult,
    PrintedRemark,
    PrintedVital,
)


# -------------------------------------------------------------------
# The answer keys are the oracle
# -------------------------------------------------------------------
def built_from_key(doc_id: str):
    record = key(doc_id)
    return record, build_record(
        document_id=record.document.document_id,
        doc_type=record.document.type,
        pages=record.pages,
        has_text_layer=record.document.has_text_layer,
        parse=parse_of(record),
    )


@pytest.mark.parametrize("doc_id", MAIN_IDS)
def test_the_record_built_from_a_keys_printed_parts_is_the_key(doc_id):
    record, built = built_from_key(doc_id)

    assert built.model_dump(mode="json") == record.model_dump(mode="json")


def _differences(a, b, path=""):
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            yield from _differences(a.get(k), b.get(k), f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            yield from _differences(x, y, f"{path}[{i}]")
    elif a != b:
        yield path, a, b


def test_where_the_code_derives_held_out_documents_differently():
    """
    The held-out documents are not used to change the code, so where the
    code reads them differently from their keys is a measured gap, listed
    here. When the code changes for another reason and closes one, this
    list changes with it.
    """
    gaps = set()
    for doc_id in HELD_OUT_IDS:
        record, built = built_from_key(doc_id)
        for path, got, expected in _differences(
            built.model_dump(mode="json"), record.model_dump(mode="json")
        ):
            gaps.add((doc_id, path, got, expected))
    assert gaps == {
        # "Glycated Haemoglobin (HbA1c)": the name with its abbreviation.
        ("d18", ".observations[5].derived.analyte", None, "hba1c"),
        # "Allergy: None known".
        ("d19", ".allergies[0].derived.no_known_allergies", False, True),
        # "TSH (Ultrasensitive)": the name with its method.
        ("d20", ".observations[1].derived.analyte", None, "tsh"),
    }


def test_the_keys_exercise_every_kind_of_derived_value():
    records = [built_from_key(d)[1] for d in PARSED_IDS]
    observations = [o for r in records for o in r.observations]
    medications = [m for r in records for m in r.medications]

    assert any(o.derived.converted for o in observations)
    assert any(o.derived.flag_computed is None and o.derived.value_num is not None
               and len(o.derived.ranges) > 1 for o in observations)  # fmt: skip
    assert any(o.derived.value_num is None for o in observations)
    assert any(m.derived.schedule and m.derived.schedule.as_needed for m in medications)
    assert any(m.derived.schedule is None for m in medications)
    assert any(m.derived.duration_days is None for m in medications)
    assert any(r.remarks_for(o.id) for r in records for o in r.observations)


def test_every_analyte_in_the_dataset_is_in_the_unit_table():
    for name, analyte in content.ANALYTES.items():
        assert lookup_analyte(name).key == analyte, name


def test_tests_without_an_analyte_get_none():
    # Urine tests share words with blood tests but are not the same analyte.
    for name in ["Glucose", "Protein", "Urine Sugar", "Urine Protein", "Pus Cells",
                 "Colour", "Specific Gravity", "Urine Creatinine"]:  # fmt: skip
        assert lookup_analyte(name) is None, name


# -------------------------------------------------------------------
# Numbers
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("10.4", 10.4), ("76", 76.0), (" 7.0 ", 7.0), ("0.27", 0.27),
        ("2,45,000", 245000.0), ("7,200", 7200.0), ("1,50,000", 150000.0),
        ("150,000", 150000.0), ("12,34,567.5", 1234567.5),
        ("< 0.5", None), ("<0.5", None), ("> 1000", None), ("2-4", None),
        ("Nil", None), ("Trace", None), ("Positive", None), ("", None),
        ("1,5", None), ("12,34", None), ("1,2345", None), (".5", None),
        ("10.4 L", None), ("-2.0", None), ("1.2.3", None), (None, None),
    ],
)  # fmt: skip
def test_parse_number_reads_only_plain_numbers(text, expected):
    assert parse_number(text) == expected


# -------------------------------------------------------------------
# Ranges
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, low, high, label, unit",
    [
        ("12.0 - 15.0", 12.0, 15.0, None, None),
        ("83-101", 83.0, 101.0, None, None),
        ("4.0 – 10.0", 4.0, 10.0, None, None),
        ("70 to 100", 70.0, 100.0, None, None),
        ("1,50,000 - 4,10,000", 150000.0, 410000.0, None, None),
        ("< 150", None, 150.0, None, None),
        ("> 60", 60.0, None, None, None),
        (">= 6.5", 6.5, None, None, None),
        ("≤ 40", None, 40.0, None, None),
        ("Up to 40", None, 40.0, None, None),
        ("Normal: < 5.7", None, 5.7, "Normal", None),
        ("Prediabetes: 5.7 - 6.4", 5.7, 6.4, "Prediabetes", None),
        ("Borderline high: 130 - 159", 130.0, 159.0, "Borderline high", None),
        ("Male: 13.0 - 17.0", 13.0, 17.0, "Male", None),
        ("1.5 - 4.5 lakh/cumm", 1.5, 4.5, None, "lakh/cumm"),
        ("< 150 mg/dL", None, 150.0, None, "mg/dL"),
        ("> 60 mL/min/1.73m²", 60.0, None, None, "mL/min/1.73m²"),
    ],
)
def test_parse_range_reads_bounds_labels_and_units(text, low, high, label, unit):
    r = parse_range(text)

    assert r.parsed
    assert (r.low, r.high, r.label, r.unit) == (low, high, label, unit)
    assert r.reference().text == text


@pytest.mark.parametrize("text", ["Nil", "Absent", "Negative", "See note",
                                  "12.0 -", "1,5 - 2", "Normal"])  # fmt: skip
def test_unparseable_ranges_keep_only_their_text(text):
    r = parse_range(text)

    assert not r.parsed
    assert r.reference().model_dump() == {
        "text": text, "low": None, "high": None, "label": None,
    }  # fmt: skip


def test_low_above_high_is_parsed_as_printed():
    # Validator 3 reports it; the parser doesn't correct the document.
    r = parse_range("15.0 - 12.0")

    assert (r.low, r.high) == (15.0, 12.0)


# -------------------------------------------------------------------
# Computed flags (decisions 11, 26)
# -------------------------------------------------------------------
def flag(value, *ranges, unit=None):
    result = compute_flag(value, [parse_range(r) for r in ranges], unit)
    return result.value if result else None


@pytest.mark.parametrize(
    "value, ranges, expected",
    [
        (10.4, ["12.0 - 15.0"], "low"),
        (12.0, ["12.0 - 15.0"], "normal"),
        (15.0, ["12.0 - 15.0"], "normal"),
        (15.1, ["12.0 - 15.0"], "high"),
        (168, ["< 150"], "high"),
        (150, ["< 150"], "high"),  # "< 150" excludes 150
        (150, ["<= 150"], "normal"),
        (149, ["< 150"], "normal"),
        (44, ["> 50"], "low"),
        (50, ["> 50"], "low"),
        (50, [">= 50"], "normal"),
        (8.1, ["Normal: < 5.7", "Prediabetes: 5.7 - 6.4", "Diabetes: >= 6.5"], None),
        (14.0, ["Male: 13 - 17", "Female: 12 - 15"], None),
        (None, ["0.0 - 5.0"], None),
        (1.0, ["Nil"], None),
        (1.0, [], None),
    ],
)
def test_a_flag_is_computed_only_when_one_range_applies(value, ranges, expected):
    assert flag(value, *ranges) == expected


def test_a_range_printed_in_another_unit_gives_no_flag():
    assert flag(13.0, "120 - 150 g/L", unit="g/dL") is None
    assert flag(1.6, "1.5 - 4.5 lakh/cumm", unit="lakh/cumm") == "normal"
    assert flag(1.6, "1.5 - 4.5 lakhs/cumm", unit="lakh/cumm") == "normal"


# -------------------------------------------------------------------
# The unit table (decision 12)
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    "spelling, symbol",
    [
        ("mg/dL", "mg/dL"), ("mg/dl", "mg/dL"), ("MG/DL", "mg/dL"),
        ("µg/dL", "µg/dL"), ("μg/dL", "µg/dL"), ("ug/dL", "µg/dL"),
        ("mcg/dL", "µg/dL"), ("µIU/mL", "µIU/mL"), ("uIU/ml", "µIU/mL"),
        ("mIU/L", "µIU/mL"), ("/cumm", "/cumm"), ("/cu mm", "/cumm"),
        ("/mm3", "/cumm"), ("/µL", "/cumm"), ("10³/µL", "10³/µL"),
        ("10^3/uL", "10³/µL"), ("x 10^9/L", "10³/µL"), ("lakhs/cumm", "lakh/cumm"),
        ("lakh/cu mm", "lakh/cumm"), ("million/cumm", "mill/cumm"),
        ("mL/min/1.73 m2", "mL/min/1.73m²"), ("IU/L", "U/L"), ("fl", "fL"),
    ],
)  # fmt: skip
def test_unit_spellings_resolve_to_one_unit(spelling, symbol):
    assert lookup_unit(spelling).symbol == symbol


def test_unknown_units_are_not_guessed():
    for text in ["mg", "units", "lakh", "", None, "mm/hr"]:
        assert lookup_unit(text) is None, text


def test_every_dataset_unit_is_in_the_table():
    for doc_id in MAIN_PARSED_IDS:
        for o in key(doc_id).observations:
            if o.printed.unit is not None:
                assert lookup_unit(o.printed.unit), o.printed.unit


def test_every_analyte_unit_is_in_the_table():
    for analyte in ANALYTES.values():
        assert analyte.canonical.symbol == analyte.unit


def test_same_unit_compares_by_the_table_then_by_text():
    assert same_unit("mg/dl", "mg/dL")
    assert not same_unit("mg/dL", "g/dL")
    assert same_unit("odd unit", "odd unit")
    assert not same_unit("odd unit", None)


def converted(value, unit, analyte):
    result = convert(value, unit, ANALYTES[analyte])
    return (result.value, result.unit, result.from_unit) if result else None


@pytest.mark.parametrize(
    "value, unit, analyte, expected",
    [
        # Counts: exact factors.
        (2.31, "lakh/cumm", "platelet_count", (231000.0, "/cumm", "lakh/cumm")),
        (6.8, "10³/µL", "wbc_count", (6800.0, "/cumm", "10³/µL")),
        (7.9, "10^9/L", "wbc_count", (7900.0, "/cumm", "10^9/L")),
        (245000, "/cumm", "platelet_count", None),  # already canonical
        (4.1, "10^12/L", "rbc_count", None),  # another spelling of mill/cumm
        # Same dimension.
        (120, "g/L", "haemoglobin", (12.0, "g/dL", "g/L")),
        (5.0, "mg/dL", "crp", (50.0, "mg/L", "mg/dL")),
        (2.4, "mIU/L", "tsh", None),  # mIU/L is µIU/mL
        # Through molar mass.
        (7.0, "mmol/L", "glucose_fasting", (126.1, "mg/dL", "mmol/L")),
        (5.2, "mmol/L", "cholesterol_total", (201.1, "mg/dL", "mmol/L")),
        (1.7, "mmol/L", "triglycerides", (150.6, "mg/dL", "mmol/L")),
        (141, "µmol/L", "creatinine", (1.595, "mg/dL", "µmol/L")),
        (7.1, "mmol/L", "bun", (19.89, "mg/dL", "mmol/L")),
        (5.0, "mmol/L", "urea", (30.03, "mg/dL", "mmol/L")),
        (17.1, "µmol/L", "bilirubin_total", (0.9998, "mg/dL", "µmol/L")),
        (50, "nmol/L", "vitamin_d_25oh", (20.03, "ng/mL", "nmol/L")),
        (138, "mEq/L", "sodium", (138.0, "mmol/L", "mEq/L")),
    ],
)  # fmt: skip
def test_conversion_to_the_canonical_unit(value, unit, analyte, expected):
    assert converted(value, unit, analyte) == expected


def test_no_conversion_without_a_number_a_known_unit_or_a_link_between_units():
    assert convert(None, "lakh/cumm", ANALYTES["platelet_count"]) is None
    assert convert(2.3, "odd/unit", ANALYTES["platelet_count"]) is None
    assert convert(2.3, "lakh/cumm", None) is None
    # No molar mass for ferritin, no valence for glucose, no % for haemoglobin.
    assert convert(20, "pmol/L", ANALYTES["ferritin"]) is None
    assert convert(5.0, "mEq/L", ANALYTES["glucose_fasting"]) is None
    assert convert(12.0, "%", ANALYTES["haemoglobin"]) is None


def test_convertible_says_whether_a_unit_fits_an_analyte():
    assert convertible("mmol/L", ANALYTES["glucose_fasting"]) is True
    assert convertible("%", ANALYTES["haemoglobin"]) is False
    assert convertible("odd/unit", ANALYTES["haemoglobin"]) is None


def test_unit_spellings_are_unique():
    # Building the table raises on a duplicate; this pins the size of it.
    assert len({u.symbol for u in UNITS.values()}) >= 25


# -------------------------------------------------------------------
# Medicines
# -------------------------------------------------------------------
def schedule(text):
    s = parse_frequency(text)
    return s.model_dump(exclude_defaults=True) if s else None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1-0-1", {"morning": 1, "noon": 0, "night": 1, "times_per_day": 2}),
        ("0 - 0 - 1", {"morning": 0, "noon": 0, "night": 1, "times_per_day": 1}),
        ("1-1-1", {"morning": 1, "noon": 1, "night": 1, "times_per_day": 3}),
        ("½-0-½", {"morning": 0.5, "noon": 0, "night": 0.5, "times_per_day": 2}),
        ("1/2-0-1", {"morning": 0.5, "noon": 0, "night": 1, "times_per_day": 2}),
        ("1½-0-0", {"morning": 1.5, "noon": 0, "night": 0, "times_per_day": 1}),
        ("2-0-2", {"morning": 2, "noon": 0, "night": 2, "times_per_day": 2}),
        ("1-1-1-1", {"times_per_day": 4}),
        ("OD", {"times_per_day": 1}), ("B.D.", {"times_per_day": 2}),
        ("bd", {"times_per_day": 2}), ("TDS", {"times_per_day": 3}),
        ("TID", {"times_per_day": 3}), ("QID", {"times_per_day": 4}),
        ("HS", {"times_per_day": 1}), ("Twice daily", {"times_per_day": 2}),
        ("SOS", {"as_needed": True}), ("PRN", {"as_needed": True}),
        ("Once a week", None), ("Weekly", None), ("STAT", None),
        ("Alternate days", None), ("1-0", None), (None, None),
    ],
)  # fmt: skip
def test_parse_frequency(text, expected):
    assert schedule(text) == expected


@pytest.mark.parametrize(
    "text, days",
    [
        ("30 days", 30), ("5 days", 5), ("1 day", 1), ("x 14 days", 14),
        ("× 5 days", 5), ("for 10 days", 10), ("7d", 7), ("5 Days.", 5),
        ("6 weeks", 42), ("1 week", 7), ("8 wks", 56),
        ("1 month", None), ("3 months", None), ("Continue", None),
        ("Till next review", None), ("", None), (None, None),
    ],
)  # fmt: skip
def test_parse_duration(text, days):
    assert parse_duration(text) == days


# -------------------------------------------------------------------
# Dates (decision 13)
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("14/06/2024", date(2024, 6, 14)),
        ("14/06/2024 02:45 PM", date(2024, 6, 14)),
        ("05/09/2024 08:30 AM", date(2024, 9, 5)),  # day first
        ("5-9-2024", date(2024, 9, 5)),
        ("05.09.2024", date(2024, 9, 5)),
        ("16-Dec-2024", date(2024, 12, 16)),
        ("20-May-2025 11:30 AM", date(2025, 5, 20)),
        ("2 April 2025", date(2025, 4, 2)),
        ("2nd Sept, 2025", date(2025, 9, 2)),
        ("2025-03-04", date(2025, 3, 4)),
        ("14/06/2024, 14:30", date(2024, 6, 14)),
        ("31/02/2025", None), ("13/13/2024", None), ("14/06/24", None),
        ("14/06-2024", None), ("16-Foo-2024", None), ("June 2024", None),
        ("", None), (None, None),
    ],
)  # fmt: skip
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def printed_text(text):
    return PrintedText(
        citation=Citation(page=1, first_line=1, last_line=1, quote=text), text=text
    )


def details(report=None, collection=None, prescription=None):
    return PrintedDocumentDetails(
        facility=None,
        clinicians=[],
        report_date=printed_text(report) if report else None,
        collection_date=printed_text(collection) if collection else None,
        prescription_date=printed_text(prescription) if prescription else None,
    )


@pytest.mark.parametrize(
    "printed, on, source",
    [
        (details("14/06/2024", "13/06/2024"), date(2024, 6, 14), "report_date"),
        (details(None, "13/06/2024"), date(2024, 6, 13), "collection_date"),
        (details(prescription="20/06/2024"), date(2024, 6, 20), "prescription_date"),
        (details(), None, "undated"),
        # A printed date that can't be read is skipped, not guessed.
        (details("31/02/2024", "13/06/2024"), date(2024, 6, 13), "collection_date"),
        (details("soon"), None, "undated"),
    ],
)
def test_the_document_date_follows_decision_13(printed, on, source):
    dates = document_dates(printed)

    assert dates.document_date == on
    assert dates.date_source == DateSource(source)


# -------------------------------------------------------------------
# Vitals, allergies, remarks
# -------------------------------------------------------------------
CITE = Citation(page=1, first_line=1, last_line=1, quote="x")


@pytest.mark.parametrize(
    "name, value, expected",
    [
        ("BP", "142/88", {"kind": "blood_pressure", "systolic": 142, "diastolic": 88}),
        ("Blood Pressure", "120 / 80", {"kind": "blood_pressure", "systolic": 120,
                                        "diastolic": 80}),
        ("BP", "142", {"kind": "blood_pressure"}),
        ("Pulse", "82", {"kind": "pulse", "value_num": 82}),
        ("HR", "82", {"kind": "pulse", "value_num": 82}),
        ("Temp", "98.4", {"kind": "temperature", "value_num": 98.4}),
        ("SpO2", "98", {"kind": "spo2", "value_num": 98}),
        ("Wt.", "66.5", {"kind": "weight", "value_num": 66.5}),
        ("BMI", "28.4", {"kind": "bmi", "value_num": 28.4}),
        ("RR", "18", {"kind": "respiratory_rate", "value_num": 18}),
        ("Pain score", "4", {"value_num": 4}),
    ],
)  # fmt: skip
def test_vitals(name, value, expected):
    v = vital(
        "vit-1", PrintedVital(citation=CITE, name=name, value_text=value, unit=None)
    )

    assert v.derived.model_dump(mode="json", exclude_none=True) == expected


@pytest.mark.parametrize(
    "text, none_known",
    [("NKDA", True), ("N.K.D.A.", True), ("No known allergies", True),
     ("Nil", True), ("Sulfa drugs (rash)", False), ("Penicillin - hives", False)],
)  # fmt: skip
def test_no_known_allergies(text, none_known):
    a = allergy("alg-1", PrintedAllergy(citation=CITE, text=text, substance=None,
                                        reaction=None))  # fmt: skip

    assert a.derived.no_known_allergies is none_known


def obs(item_id, test_name, panel):
    return Observation(
        id=item_id,
        printed=PrintedLabResult(
            citation=CITE,
            panel=panel,
            test_name=test_name,
            value_text="1",
            unit=None,
            reference_ranges=[],
            flag_printed=None,
        ),  # fmt: skip
        derived=ObservationDerived(),
    )


def printed_remark(*, scope="test", test_name="HbA1c", panel="GLYCATED HAEMOGLOBIN"):
    return PrintedRemark(citation=CITE, text="Note.", kind="comment", scope=scope,
                         test_name=test_name, panel=panel)  # fmt: skip


def test_a_test_remark_links_to_its_observation():
    observations = [
        obs("obs-1", "HbA1c", "GLYCATED HAEMOGLOBIN"),
        obs("obs-2", "TSH", "T"),
    ]

    assert (
        remark("rem-1", printed_remark(), observations).derived.observation_id
        == "obs-1"
    )
    # Whitespace differences don't matter; a missing panel falls back to the name.
    loose = printed_remark(test_name="HbA1c ", panel=None)
    assert remark("rem-1", loose, observations).derived.observation_id == "obs-1"


def test_no_link_when_the_test_is_missing_or_ambiguous():
    twice = [obs("obs-1", "Glucose", "BLOOD"), obs("obs-2", "Glucose", "URINE")]

    assert (
        remark("r", printed_remark(test_name="TSH"), twice).derived.observation_id
        is None
    )
    ambiguous = printed_remark(test_name="Glucose", panel=None)
    assert remark("r", ambiguous, twice).derived.observation_id is None
    by_panel = printed_remark(test_name="Glucose", panel="URINE")
    assert remark("r", by_panel, twice).derived.observation_id == "obs-2"


def test_panel_and_document_remarks_have_no_link():
    observations = [obs("obs-1", "HbA1c", "GLYCATED HAEMOGLOBIN")]
    on_panel = printed_remark(scope="panel", test_name=None)

    assert remark("r", on_panel, observations).derived.observation_id is None


def test_a_document_of_type_other_holds_only_its_pages():
    record, built = built_from_key("d17")

    assert built.patient is None and list(built.items()) == []
    assert built.document.derived.date_source is DateSource.UNDATED
    assert built.pages == record.pages
