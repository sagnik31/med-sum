"""
The eval scorer, tested on the answer keys: a key scored against itself is
perfect, and each kind of mistake a model can make costs what it should.
"""

import json
import random

import pytest

from dataset_records import DOC_IDS, key
from evals import Counts, Ratio, score_document, summarise
from evals.score import IDENTITY_MATCH, INVENTED, similarity
from record.printed import DocumentType, RemarkScope


def score(pred, doc_id):
    return score_document(pred, key(doc_id))


def by_test(record, name):
    return next(o for o in record.observations if o.printed.test_name == name)


# -------------------------------------------------------------------
# A perfect prediction
# -------------------------------------------------------------------
@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_a_key_scores_perfectly_against_itself(doc_id):
    s = score(key(doc_id), doc_id)
    assert s.type_correct
    assert s.errors == [] and s.missed == [] and s.hallucinated == []
    assert s.all_fields.fp == s.all_fields.fn == 0
    assert s.all_derived.fp == s.all_derived.fn == 0
    assert s.citations.correct == s.citations.total
    assert s.remark_attribution.correct == s.remark_attribution.total
    assert s.frequency.correct == s.frequency.total


def test_every_kind_of_score_is_exercised_by_the_dataset():
    total = summarise(score(key(d), d) for d in DOC_IDS)
    assert total.documents == len(DOC_IDS)
    for group in ("patient", "document", "observations", "medications",
                  "conditions", "remarks", "vitals", "allergies"):  # fmt: skip
        assert total.fields[group].tp > 0, group
    assert total.citations.total > 0
    assert total.remark_attribution.total > 0
    assert total.frequency.total > 0
    assert total.all_derived.tp > 0


@pytest.mark.parametrize("doc_id", ["d01", "d02", "d03"])
def test_ids_line_numbers_and_order_do_not_matter(doc_id):
    pred = key(doc_id)
    rename = {}
    for item in pred.items():
        rename[item.id] = item.id = f"x-{item.id}"
    for r in pred.remarks:
        if r.derived.observation_id is not None:
            r.derived.observation_id = rename[r.derived.observation_id]
    for item in pred.items():
        c = item.printed.citation
        c.first_line += 7
        c.last_line += 7
    for group in ("observations", "medications", "remarks", "vitals"):
        random.Random(1).shuffle(getattr(pred, group))
    s = score(pred, doc_id)
    assert s.errors == [] and s.missed == [] and s.hallucinated == []
    assert s.citations.correct == s.citations.total
    assert s.remark_attribution.correct == s.remark_attribution.total


def test_quotes_compare_without_case_spacing_or_table_pipes():
    pred = key("d01")
    c = by_test(pred, "Haemoglobin").printed.citation
    c.quote = "Haemoglobin  10.4 L g/dL 12.0 - 15.0"
    s = score(pred, "d01")
    assert s.citations.correct == s.citations.total


# -------------------------------------------------------------------
# Mistakes
# -------------------------------------------------------------------
def test_a_dropped_medicine_lowers_recall_only():
    pred = key("d02")
    dropped = pred.medications.pop(0)
    s = score(pred, "d02")
    assert s.items["medications"] == Counts(tp=2, fp=0, fn=1)
    assert s.medicine_recall == pytest.approx(2 / 3)
    assert s.fields["medications"].fp == 0
    assert s.fields["medications"].fn == sum(
        v is not None for k, v in dropped.printed if k != "citation"
    )
    assert [m.id for m in s.missed] == [dropped.id]
    assert s.hallucinated == []


def test_an_invented_medicine_is_hallucinated():
    pred = key("d02")
    extra = pred.medications[0].model_copy(deep=True)
    extra.id = "med-invented"
    extra.printed.name = "Atorva"
    extra.printed.citation.quote = "Tab. Atorva 10 mg 0-0-1"
    pred.medications.append(extra)
    s = score(pred, "d02")
    assert s.items["medications"] == Counts(tp=3, fp=1, fn=0)
    assert [(h.group, h.id, h.label) for h in s.hallucinated] == [
        ("medications", "med-invented", "Atorva")
    ]
    assert s.fields["medications"].fp > 0 and s.fields["medications"].fn == 0


def test_a_wrong_value_costs_one_false_positive_and_one_false_negative():
    pred = key("d01")
    obs = by_test(pred, "Haemoglobin")
    obs.printed.value_text = "10.9"
    obs.derived.value_num = 10.9
    s = score(pred, "d01")
    assert s.fields["observations"].fp == 1 and s.fields["observations"].fn == 1
    errors = {(e.field, e.expected, e.got) for e in s.errors}
    assert ("value_text", "10.4", "10.9") in errors
    assert ("derived.value_num", 10.4, 10.9) in errors
    assert s.missed == [] and s.hallucinated == []


def test_values_compare_case_sensitively_but_ignore_spacing_and_unicode_forms():
    pred = key("d01")
    hb = by_test(pred, "Haemoglobin")
    hb.printed.unit = " g/dL "  # spacing only: still right
    hb.printed.reference_ranges = ["12.0  -  15.0"]
    assert score(pred, "d01").errors == []
    hb.printed.unit = "G/DL"
    assert [e.field for e in score(pred, "d01").errors] == ["unit"]


def test_a_misspelt_test_name_still_pairs_and_costs_one_field():
    pred = key("d01")
    by_test(pred, "Haemoglobin").printed.test_name = "Hemoglobin"
    s = score(pred, "d01")
    assert s.missed == [] and s.hallucinated == []
    assert [(e.field, e.expected, e.got) for e in s.errors] == [
        ("test_name", "Haemoglobin", "Hemoglobin")
    ]


def test_an_unrelated_test_name_does_not_pair():
    pred = key("d01")
    by_test(pred, "Haemoglobin").printed.test_name = "Serum Ferritin"
    s = score(pred, "d01")
    assert [m.label for m in s.missed] == ["Haemoglobin"]
    assert [h.label for h in s.hallucinated] == ["Serum Ferritin"]


def test_rows_with_the_same_name_pair_by_their_quotes():
    expected, pred = key("d01"), key("d01")
    for record in (expected, pred):
        for o in record.observations[:2]:
            o.printed.test_name = "Same Test"
    pred.observations[0], pred.observations[1] = (
        pred.observations[1],
        pred.observations[0],
    )
    s = score_document(pred, expected)
    assert s.errors == [] and s.missed == [] and s.hallucinated == []


def test_a_missing_field_is_a_false_negative_and_an_extra_one_a_false_positive():
    pred = key("d01")
    hb = by_test(pred, "Haemoglobin")
    hb.printed.flag_printed = None
    rbc = by_test(pred, "RBC Count")
    rbc.printed.flag_printed = "H"
    s = score(pred, "d01")
    assert s.fields["observations"].fn == 1 and s.fields["observations"].fp == 1
    assert {(e.field, e.expected, e.got) for e in s.errors} == {
        ("flag_printed", "L", None),
        ("flag_printed", None, "H"),
    }


def test_reference_ranges_compare_as_a_multiset():
    pred = key("d01")
    obs = next(o for o in pred.observations if len(o.printed.reference_ranges) > 1)
    n = len(obs.printed.reference_ranges)
    obs.printed.reference_ranges = obs.printed.reference_ranges[::-1]
    assert score(pred, "d01").fields["observations"].fn == 0  # order is ignored
    obs.printed.reference_ranges = obs.printed.reference_ranges[1:]
    c = score(pred, "d01").fields["observations"]
    assert (c.fn, c.fp) == (1, 0)
    assert n > 1


def test_a_citation_on_the_wrong_page_is_wrong():
    pred = key("d01")
    by_test(pred, "Haemoglobin").printed.citation.page = 2
    s = score(pred, "d01")
    assert s.citations.correct == s.citations.total - 1


def test_a_citation_quoting_another_line_is_wrong():
    pred = key("d01")
    hb, rbc = by_test(pred, "Haemoglobin"), by_test(pred, "RBC Count")
    hb.printed.citation.quote = rbc.printed.citation.quote
    s = score(pred, "d01")
    assert s.citations.correct == s.citations.total - 1
    assert s.errors == []  # the item still pairs by its test name


# -------------------------------------------------------------------
# Remark attribution (remarks put on the wrong tests)
# -------------------------------------------------------------------
def test_a_panel_remark_attached_to_its_last_test_is_misattributed():
    pred = key("d01")
    remark = next(r for r in pred.remarks if r.printed.scope is RemarkScope.PANEL)
    remark.printed.scope = RemarkScope.TEST
    remark.printed.test_name = "WBC Count"
    s = score(pred, "d01")
    assert s.remark_attribution == Ratio(correct=1, total=2)
    assert {e.field for e in s.errors} == {"scope", "test_name"}


def test_a_test_remark_linked_to_the_wrong_observation_is_misattributed():
    pred = key("d01")
    remark = next(r for r in pred.remarks if r.printed.scope is RemarkScope.TEST)
    other = next(o for o in pred.observations if o.id != remark.derived.observation_id)
    remark.derived.observation_id = other.id
    s = score(pred, "d01")
    assert s.remark_attribution == Ratio(correct=1, total=2)


def test_a_document_remark_moved_to_a_panel_is_misattributed():
    pred = key("d06")
    remark = pred.remarks[0]
    assert remark.printed.scope is RemarkScope.DOCUMENT
    remark.printed.scope = RemarkScope.PANEL
    remark.printed.panel = pred.observations[0].printed.panel
    assert score(pred, "d06").remark_attribution == Ratio(correct=0, total=1)


# -------------------------------------------------------------------
# Frequency exactness
# -------------------------------------------------------------------
def test_a_wrong_frequency_is_counted_against_frequency_exactness():
    pred = key("d02")
    med = next(m for m in pred.medications if m.printed.frequency == "1-0-1")
    med.printed.frequency = "1-1-1"
    med.derived.schedule.noon = 1.0
    med.derived.schedule.times_per_day = 3.0
    s = score(pred, "d02")
    assert s.frequency.correct == s.frequency.total - 1
    assert "derived.schedule" in {e.field for e in s.errors}


def test_a_right_frequency_text_with_a_wrong_schedule_is_not_exact():
    pred = key("d02")
    med = next(m for m in pred.medications if m.printed.frequency == "1-0-1")
    med.derived.schedule.night = 0.0
    s = score(pred, "d02")
    assert s.frequency.correct == s.frequency.total - 1
    assert s.fields["medications"].fn == 0  # the printed reading was right


# -------------------------------------------------------------------
# Headers, types, failed runs
# -------------------------------------------------------------------
def test_patient_and_document_header_fields_are_scored():
    pred = key("d01")
    pred.patient.printed.name.text = "Mrs. Meera Iyengar"
    pred.document.printed.clinicians.pop()
    s = score(pred, "d01")
    assert s.fields["patient"].fp == 1 and s.fields["patient"].fn == 1
    assert s.fields["document"].fn == 1 and s.fields["document"].fp == 0
    assert {e.field for e in s.errors} == {"name", "clinicians"}


def test_document_dates_are_scored_as_derived_values():
    pred = key("d01")
    pred.document.derived.report_date = None
    s = score(pred, "d01")
    assert s.derived["document"].fn == 1
    assert [e.field for e in s.errors] == ["derived.report_date"]


def test_a_wrong_type_is_reported():
    pred = key("d17")
    pred.document.type = DocumentType.LAB_REPORT
    s = score(pred, "d17")
    assert not s.type_correct and s.type_got == "lab_report"


def test_a_failed_run_misses_everything():
    s = score(None, "d02")
    assert s.type_got is None and not s.type_correct
    assert s.all_fields.tp == 0 and s.all_fields.fp == 0 and s.all_fields.fn > 0
    assert s.all_fields.recall == 0.0 and s.all_fields.precision is None
    assert s.medicine_recall == 0.0
    assert len(s.missed) == sum(1 for _ in key("d02").items())


def test_an_other_document_scores_only_its_type():
    s = score(key("d17"), "d17")
    assert s.type_correct
    assert s.all_fields == Counts(tp=s.all_fields.tp)
    assert s.citations.total == len(key("d17").document.printed.clinicians) + sum(
        getattr(key("d17").document.printed, f) is not None
        for f in ("facility", "report_date", "collection_date", "prescription_date")
    )


# -------------------------------------------------------------------
# Totals and output
# -------------------------------------------------------------------
def test_counts_and_ratios():
    c = Counts(tp=3, fp=1, fn=2)
    assert c.precision == 0.75 and c.recall == 0.6
    assert c.f1 == pytest.approx(2 * 3 / (2 * 3 + 1 + 2))
    assert Counts().precision is None and Counts().f1 is None
    assert Counts(1, 2, 3) + Counts(4, 5, 6) == Counts(5, 7, 9)
    assert Ratio().value is None and (Ratio(1, 2) + Ratio(1, 2)).value == 0.5


def test_summary_adds_documents_up():
    a, b = score(key("d01"), "d01"), score(None, "d02")
    total = summarise([a, b])
    assert total.documents == 2 and total.types == Ratio(correct=1, total=2)
    assert total.all_fields == a.all_fields + b.all_fields
    assert total.missed == len(b.missed)
    assert total.medicine_recall == 0.0


def test_scores_serialise_to_json():
    pred = key("d01")
    by_test(pred, "Haemoglobin").printed.value_text = "10.9"
    s = score(pred, "d01")
    doc = json.loads(json.dumps(s.as_dict()))
    assert doc["errors"] and doc["fields"]["observations"]["fp"] == 1
    total = json.loads(json.dumps(summarise([s]).as_dict()))
    assert total["documents"] == 1


# -------------------------------------------------------------------
# Pairing threshold
# -------------------------------------------------------------------
def test_a_name_less_than_90_percent_alike_does_not_pair():
    assert 0.8 <= similarity("hb haemoglobin", "haemoglobin") < IDENTITY_MATCH
    pred = key("d01")
    by_test(pred, "Haemoglobin").printed.test_name = "Hb Haemoglobin"
    s = score(pred, "d01")
    assert [m.label for m in s.missed] == ["Haemoglobin"]
    assert [h.label for h in s.hallucinated] == ["Hb Haemoglobin"]


# -------------------------------------------------------------------
# Unsafe errors (the model-choice gate)
# -------------------------------------------------------------------
@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_a_perfect_prediction_has_no_unsafe_errors(doc_id):
    s = score(key(doc_id), doc_id)
    assert s.unsafe == []
    k = key(doc_id)
    assert s.clinical_items == len(k.observations) + len(k.medications) + len(
        k.allergies
    )


def test_invented_results_medicines_and_allergies_are_unsafe():
    pred = key("d02")
    extra = pred.medications[0].model_copy(deep=True)
    extra.id, extra.printed.name = "med-invented", "Atorva"
    pred.medications.append(extra)
    allergy = pred.allergies[0].model_copy(deep=True)
    allergy.id, allergy.printed.text = "alg-invented", "Allergic to penicillin"
    pred.allergies.append(allergy)
    s = score(pred, "d02")
    assert {(u.group, u.pred_id, u.field) for u in s.unsafe} == {
        ("medications", "med-invented", INVENTED),
        ("allergies", "alg-invented", INVENTED),
    }
    assert s.unsafe_rate == pytest.approx(2 / 4)  # 3 medicines + 1 allergy


def test_an_invented_remark_or_vital_is_not_unsafe():
    pred = key("d02")
    vital = pred.vitals[0].model_copy(deep=True)
    vital.id, vital.printed.name = "vit-invented", "Respiratory Rate"
    pred.vitals.append(vital)
    s = score(pred, "d02")
    assert s.hallucinated and s.unsafe == []


def test_wrong_or_invented_clinical_values_are_unsafe_but_missing_ones_are_not():
    pred = key("d01")
    by_test(pred, "Haemoglobin").printed.value_text = "10.9"  # wrong: unsafe
    by_test(pred, "RBC Count").printed.flag_printed = "H"  # invented: unsafe
    by_test(pred, "Haemoglobin").printed.flag_printed = None  # missing: not
    by_test(pred, "RBC Count").printed.panel = "CBC"  # not a clinical field
    s = score(pred, "d01")
    assert sorted((u.field, u.expected, u.got) for u in s.unsafe) == [
        ("flag_printed", None, "H"),
        ("value_text", "10.4", "10.9"),
    ]


def test_a_wrong_medicine_strength_is_unsafe():
    pred = key("d02")
    med = next(m for m in pred.medications if m.printed.strength is not None)
    med.printed.strength = "1000 mg"
    s = score(pred, "d02")
    assert [(u.field, u.got) for u in s.unsafe] == [("strength", "1000 mg")]


def test_unsafe_errors_add_up_in_the_summary():
    pred = key("d01")
    by_test(pred, "Haemoglobin").printed.value_text = "10.9"
    total = summarise([score(pred, "d01"), score(key("d02"), "d02")])
    assert total.unsafe == 1 and total.clinical_items == 26 + 4
    assert total.unsafe_rate == pytest.approx(1 / 30)
    assert json.loads(json.dumps(total.as_dict()))["unsafe"] == 1
