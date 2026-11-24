"""
The patient summary from records (summary/, views/summary.py): the facts,
the overview check, and the page. One test per accuracy bug that the
model-written summary showed, plus the summary's rules.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import llm
from dataset_records import key
from pipeline import signals
from pipeline.signals import SIGNALS, SignalHit
from record.common import Provenance
from record.document import DateSource
from summary.facts import SummaryInput, _specimen, build_facts, trend
from summary.overview import Overview, check, facts_text, write_overview
from views.summary import summary_html

NOW = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc)
JUDGED = (
    Path(__file__).parents[1] / "evals/reports/2026-11-22-suite-overview/judge.json"
)
PATIENTS = {
    "meera": ["d01", "d02", "d03", "d04", "d05"],
    "rohan": ["d06", "d07", "d08"],
    "fatima": ["d09", "d10", "d11", "d12"],
    "arjun": ["d13", "d14", "d15", "d16", "d17"],
}
JUDGEMENTS = ("worsening", "improving", "improved", "deteriorat", "abnormal",
              "confidence", "acute kidney injury", "aki", "critical", "severe",
              "cardiovascular", "renal system", "lipid status")  # fmt: skip


def inputs(*doc_ids, records=None, status="verified") -> list[SummaryInput]:
    """The documents uploaded in the order given, one day apart."""
    records = records or {}
    return [
        SummaryInput(
            document_id=f"id-{d}",
            name=f"{d}.pdf",
            uploaded_at=datetime(2026, 1, n + 1, tzinfo=timezone.utc),
            status=status,
            record=records.get(d) or key(d),
        )
        for n, d in enumerate(doc_ids)
    ]


def page(ins, overview=None):
    facts = build_facts(ins)
    html = summary_html(facts, overview or Overview(None, "no_model"), generated_at=NOW)
    return facts, html, BeautifulSoup(html, "html.parser")


def marked(doc_id, hits):
    """A dataset record with provenance, and the given signals applied."""
    record = key(doc_id).model_copy(deep=True)
    for holder in [record.document, record.patient, *record.items()]:
        if holder is not None:
            holder.provenance = Provenance(run_id="r", node="parse")
    signals.apply(record, hits)
    return record


def series(facts, analyte):
    [s] = [s for s in facts.labs if s.key == f"analyte:{analyte}"]
    return s


# -------------------------------------------------------------------
# The early model summaries' mistakes, one test each.
# Remarks attached to the wrong tests
# -------------------------------------------------------------------
def test_remarks_stay_on_what_they_are_printed_on():
    _, _, soup = page(inputs(*PATIENTS["meera"]))
    remarks = soup.select('[data-section="remarks"] li')

    # d01's CBC comment is on the panel, not on the WBC row above it.
    cbc = [r for r in remarks if "on the panel" in r.get_text()]
    assert cbc and all("WBC" not in r.get_text() for r in cbc)
    # d03's "Suggest iron studies" is on the document, not on HbA1c.
    [iron] = [r for r in remarks if "Suggest iron studies" in r.get_text()]
    assert iron["data-scope"] == "document" and "HbA1c" not in iron.get_text()
    # Remarks never appear inside the results table rows.
    labs = soup.select_one('[data-section="labs"]').get_text()
    assert "Suggest iron studies" not in labs
    assert "Microcytic hypochromic anaemia" not in labs


# -------------------------------------------------------------------
# A diagnosis invented from a single value
# -------------------------------------------------------------------
def test_a_single_creatinine_never_becomes_a_diagnosis():
    facts, html, _ = page(inputs(*PATIENTS["rohan"]))

    assert "kidney injury" not in html.lower() and "AKI" not in html
    # The only kidney diagnosis is the one printed, verbatim.
    assert [c.item.printed.text for c in facts.conditions] == [
        "Hypertension", "Chronic kidney disease, to be evaluated",
    ]  # fmt: skip
    lines = facts_text(facts)
    assert not check(
        "The raised creatinine indicates acute kidney injury.", lines
    ).follows_facts


# -------------------------------------------------------------------
# "worsening" from a single data point
# -------------------------------------------------------------------
def test_one_value_has_no_trend():
    facts, html, _ = page(inputs(*PATIENTS["rohan"]))

    creatinine = series(facts, "creatinine")
    assert len(creatinine.values) == 1 and creatinine.trend is None
    assert "data-trend" not in html
    assert not check("Creatinine is worsening.", facts_text(facts)).follows_facts


# -------------------------------------------------------------------
# A confidence label from one report
# -------------------------------------------------------------------
def test_no_confidence_labels_only_counts_of_reports():
    facts, _, soup = page(inputs(*PATIENTS["fatima"]))

    tsh = soup.select_one('[data-series="analyte:tsh"]')
    trend_text = tsh.select_one("[data-trend]").get_text(" ", strip=True)
    assert trend_text == "falling, based on 2 reports (D1, D3) derived"
    assert "confidence" not in soup.get_text().lower()
    # Each value the trend counts is cited.
    assert len(tsh.select("cite")) == 2
    assert not check(
        "TSH is falling with high confidence.", facts_text(facts)
    ).follows_facts


# -------------------------------------------------------------------
# Upload order taken for the timeline
# -------------------------------------------------------------------
def test_documents_are_ordered_by_their_own_date_not_upload():
    # d04 (2025-01-10) is uploaded before the 2024 documents.
    facts, _, soup = page(inputs("d04", "d01", "d02", "d03", "d05"))

    assert [d.document_id for d in facts.documents] == [
        "id-d01", "id-d02", "id-d03", "id-d04", "id-d05",
    ]  # fmt: skip
    dates = [
        r.select("td")[1].get_text()
        for r in soup.select('[data-section="timeline"] tr')
    ]
    assert dates == sorted(dates)
    # The latest prescription is the 2025 one, and the 2024 one is earlier.
    assert facts.latest_prescription.doc.document_id == "id-d04"
    assert [p.doc.document_id for p in facts.earlier_prescriptions] == ["id-d02"]


# -------------------------------------------------------------------
# Tests filed under an organ system
# -------------------------------------------------------------------
def test_tests_stay_under_their_printed_panel():
    facts, _, soup = page(inputs(*PATIENTS["rohan"]))

    [bun] = [s for s in facts.labs if "BUN" in s.name]
    labs = soup.select_one('[data-section="labs"]')
    panel = labs.find("tr", attrs={"data-series": bun.key}).find_previous("h3")
    assert (
        panel.get_text()
        == bun.panel
        == key("d06")
        .observations[
            [o.printed.test_name for o in key("d06").observations].index(bun.name)
        ]
        .printed.panel
    )
    assert "cardiovascular" not in soup.get_text().lower()


# -------------------------------------------------------------------
# Lipid status guessed from a statin
# -------------------------------------------------------------------
def test_a_statin_without_a_lipid_panel_gives_no_lipid_facts():
    facts, html, _ = page(inputs(*PATIENTS["rohan"]))
    lines = facts_text(facts)

    assert "Atorva" in lines  # the statin is listed as printed
    assert "lipid" not in lines.lower() and "cholesterol" not in lines.lower()
    assert "lipid" not in html.lower() and "cholesterol" not in html.lower()
    assert not check("Lipids are controlled on Atorva.", lines).follows_facts


@pytest.mark.parametrize("patient", sorted(PATIENTS))
def test_no_summary_states_a_judgement_its_records_do_not_hold(patient):
    ins = inputs(*PATIENTS[patient])
    printed = " ".join(
        str(v) for i in ins for v in i.record.model_dump(mode="json").values()
    ).lower()
    _, _, soup = page(ins)
    text = soup.get_text(" ").lower()
    for word in JUDGEMENTS:
        if word not in printed:
            assert word not in text, word


# -------------------------------------------------------------------
# Trends: rising or falling, 2+ values, same unit
# -------------------------------------------------------------------
def tsh_records(*values, units=None, dates=None):
    """d09 copies, each with its TSH value, unit and date replaced."""
    out = {}
    base = key("d09")
    idx = [o.derived.analyte for o in base.observations].index("tsh")
    for n, value in enumerate(values):
        r = base.model_copy(deep=True)
        o = r.observations[idx]
        o.printed.value_text = str(value)
        o.derived.value_num = float(value)
        if units:
            o.printed.unit = units[n]
        d = (dates or [date(2024, 1 + n, 5) for n in range(len(values))])[n]
        r.document.derived = r.document.derived.model_copy(
            update={
                "report_date": d,
                "document_date": d,
                "date_source": r.document.derived.date_source,
            }
        )
        out[f"t{n}"] = r
    return out


def tsh_trend(records):
    facts = build_facts(inputs(*records, records=records))
    return series(facts, "tsh").trend


def test_two_rising_values_in_one_unit_are_rising():
    t = tsh_trend(tsh_records(2.0, 4.5))
    assert (t.direction, len(t.values)) == ("rising", 2)


def test_three_falling_values_are_falling_based_on_three_reports():
    t = tsh_trend(tsh_records(8.0, 5.0, 3.0))
    assert (t.direction, len(t.values)) == ("falling", 3)


def test_values_that_go_up_and_down_have_no_trend():
    assert tsh_trend(tsh_records(2.0, 5.0, 3.0)) is None


def test_equal_values_have_no_trend():
    assert tsh_trend(tsh_records(3.0, 3.0)) is None


def test_values_in_different_units_are_not_compared():
    records = tsh_records(2.0, 4.5, units=["µIU/mL", "mIU/L"])
    assert tsh_trend(records) is None


def test_two_values_on_the_same_date_have_no_trend():
    same = [date(2024, 3, 1), date(2024, 3, 1)]
    assert tsh_trend(tsh_records(2.0, 4.5, dates=same)) is None


def test_a_value_awaiting_review_is_not_used_for_a_trend():
    records = tsh_records(2.0, 4.5, 6.0)
    idx = [o.derived.analyte for o in records["t2"].observations].index("tsh")
    flagged = records["t2"].observations[idx].id
    for holder in [
        records["t2"].document,
        records["t2"].patient,
        *records["t2"].items(),
    ]:
        holder.provenance = Provenance(run_id="r", node="parse")
    signals.apply(
        records["t2"], [SignalHit(signal="LC-04", item_id=flagged, field="value_text")]
    )

    t = tsh_trend(records)
    assert (t.direction, len(t.values)) == ("rising", 2)


def test_trend_needs_dates():
    records = tsh_records(2.0, 4.5)
    r = records["t1"]
    r.document.derived = r.document.derived.model_copy(
        update={"report_date": None, "collection_date": None, "document_date": None,
                "date_source": DateSource.UNDATED}
    )  # fmt: skip
    assert trend(build_facts(inputs(*records, records=records)).labs[0].values) is None


# -------------------------------------------------------------------
# Undated documents
# -------------------------------------------------------------------
def test_undated_documents_go_last_and_are_never_latest():
    facts, _, soup = page(inputs("d15", *PATIENTS["arjun"][:2]))  # d15 uploaded first

    assert facts.documents[-1].document_id == "id-d15"
    assert facts.latest_prescription.doc.document_id == "id-d14"
    assert [p.doc.document_id for p in facts.earlier_prescriptions] == ["id-d15"]
    warnings = soup.select_one('[data-section="warnings"]').get_text()
    assert "D3 is undated" in warnings
    last = soup.select('[data-section="timeline"] tr')[-1]
    assert last.select("td")[1].get_text() == "undated"


# -------------------------------------------------------------------
# Documents awaiting review are included (flagged values marked)
# -------------------------------------------------------------------
def test_a_document_awaiting_review_is_included_with_its_flags():
    d11 = marked(
        "d11", [SignalHit(signal="LC-10", item_id="obs-1", field="flag_printed")]
    )
    d16 = marked("d16", [SignalHit(signal="LC-13")])
    ins = [
        *inputs("d09"),
        *inputs("d11", records={"d11": d11}, status="needs_review"),
        *inputs("d16", records={"d16": d16}, status="needs_review"),
    ]
    facts, _, soup = page(ins)

    assert len(facts.documents) == 3
    item = soup.find("li", attrs={"data-item": "obs-1", "data-doc": "D2"})
    assert "awaiting review" in item.get_text()
    assert SIGNALS["LC-10"].reason in item.get_text()
    warnings = soup.select_one('[data-section="warnings"]').get_text(" ")
    assert SIGNALS["LC-13"].reason in warnings  # document-level, at its document
    # d16 is printed for another patient: each of its values awaits review.
    d16_values = 1 + len(list(d16.items()))  # the patient details too
    assert facts.awaiting == 1 + d16_values
    awaiting = soup.select_one('[data-warning="awaiting"]').get_text()
    assert awaiting.startswith(f"{1 + d16_values} ")
    timeline = soup.select('[data-section="timeline"] tr')
    assert [r.select("td")[-1].get_text() for r in timeline] == [
        "verified", "awaiting review", "awaiting review",
    ]  # fmt: skip


def test_documents_without_a_finished_record_are_left_out_and_counted():
    ins = inputs("d09", "d11")
    ins[1] = SummaryInput("id-x", "x.pdf", ins[1].uploaded_at, "processing", key("d11"))
    ins.append(SummaryInput("id-y", "y.pdf", ins[1].uploaded_at, "failed", None))
    facts, _, soup = page(ins)

    assert [d.document_id for d in facts.documents] == ["id-d09"]
    assert facts.not_included == 2
    assert soup.select_one('[data-warning="not_included"]').get_text().startswith("2 ")


def test_a_document_of_type_other_is_in_the_timeline_only():
    facts, _, soup = page(inputs("d13", "d17"))
    assert [d.type.value for d in facts.documents] == ["lab_report", "other"]
    assert "D2 is of a type that isn't parsed" in soup.get_text()


# -------------------------------------------------------------------
# Needs attention, medicines, citations
# -------------------------------------------------------------------
def test_needs_attention_lists_the_latest_value_of_each_flagged_test():
    facts, _, _ = page(inputs(*PATIENTS["meera"]))
    hb = [f for f in facts.attention if f.item.derived.analyte == "haemoglobin"]
    # Haemoglobin is in three reports; only the latest (d05) is listed.
    assert [(f.doc.document_id, f.item.printed.value_text) for f in hb] == [
        ("id-d05", "11.9")
    ]


def test_a_flagged_value_is_given_as_the_latest_value_with_its_own_date():
    # Meera's latest lab report is dated 2025-04-12, but some tests were
    # last measured, and flagged, on 2024-06-14. Their lines must not say
    # they are in the latest report.
    facts = build_facts(inputs(*PATIENTS["meera"]))
    lines = facts_text(facts).splitlines()
    flagged = [line for line in lines if line.startswith("Latest flagged value: ")]

    assert len(flagged) == len(facts.attention)
    assert not [line for line in lines if "latest report" in line.lower()]
    for f, line in zip(facts.attention, flagged):
        assert f.item.printed.test_name in line
        assert line.endswith(f", {f.doc.date.isoformat()}.")
    assert {f.doc.date.isoformat() for f in facts.attention} >= {
        "2024-06-14",
        "2025-04-12",
    }


def test_no_flagged_value_is_said_as_none():
    lines = facts_text(build_facts(inputs("d12")))  # a prescription only
    assert "Latest flagged values: none." in lines.splitlines()


def test_every_value_cites_its_document_page_and_lines():
    facts, _, soup = page(inputs(*PATIENTS["fatima"]))
    cites = soup.select("cite")
    assert cites
    for c in cites:
        n = int(c["data-doc"][1:])
        record = facts.documents[n - 1].record
        # The app opens the document from this id.
        assert c["data-document-id"] == facts.documents[n - 1].document_id
        first, _, last = c["data-lines"].partition("-")
        lines = record.pages[int(c["data-page"]) - 1].lines
        assert 1 <= int(first) <= int(last or first) <= len(lines)


# -------------------------------------------------------------------
# The overview check
# -------------------------------------------------------------------
FATIMA_LINES = facts_text(build_facts(inputs(*PATIENTS["fatima"])))


@pytest.mark.parametrize(
    "text",
    [
        "The 4 documents are dated 2024-09-05 to 2025-03-15.",
        "TSH is falling, based on 2 reports.",
        "The latest prescription lists Thyronorm and Uprise-D3.",
        "Hypothyroidism and Vitamin D deficiency are stated diagnoses.",
        "An allergy to Penicillin is recorded.",
        # Values beside what they are, in the overview's own order.
        "Vitamin D (25-OH) is 18.4 ng/mL, flagged L, on 2025-01-08.",
        "The latest prescription, dated 2025-03-15, lists Thyronorm 50 mcg 1-0-0.",
        "There are 4 documents.",
    ],
)
def test_an_overview_made_of_the_facts_passes(text):
    assert check(text, FATIMA_LINES).follows_facts


def test_a_live_overview_of_one_report_passes():
    # qwen3.5:4b's reply for d09 alone, on a live run.
    lines = facts_text(build_facts(inputs("d09")))
    reply = (
        "The patient has one lab report dated 2024-09-05. The latest report flags "
        "TSH at 8.9 µIU/mL as high. No trends are listed. No prescriptions or "
        "diagnoses are stated."
    )
    assert check(reply, lines).follows_facts


@pytest.mark.parametrize(
    "text, numbers, words",
    [
        ("TSH fell to 2.5.", 1, 0),  # a number not in the facts
        ("The latest prescription lists Eltroxin.", 0, 1),  # a medicine
        # A test, though "4" is a number of the facts: "T4" is checked whole.
        ("Free T4 was not tested.", 1, 3),
        # A fact's value beside another test.
        ("TSH was 18.4 ng/mL.", 1, 0),
        # A fact's date for something it isn't the date of.
        ("Hypothyroidism was stated on 2025-01-08.", 1, 0),
        # A fact's count for something else.
        ("There are 4 diagnoses.", 1, 0),
        ("Graves disease is stated.", 0, 2),  # a diagnosis
        ("TSH is improving.", 0, 1),  # a judgement
        ("Her thyroid is well controlled.", 0, 4),
    ],
)
def test_an_overview_with_anything_outside_the_facts_fails(text, numbers, words):
    result = check(text, FATIMA_LINES)
    assert not result.follows_facts
    assert (result.unknown_numbers, result.unknown_words) == (numbers, words)


def test_a_failed_check_leaves_the_overview_out_and_says_why():
    facts = build_facts(inputs(*PATIENTS["rohan"]))

    overview = write_overview(
        facts, "prompt", chat=lambda *a, **k: "Acute kidney injury is likely."
    )
    html = summary_html(facts, overview, generated_at=NOW)

    assert (overview.outcome, overview.text) == ("rejected", None)
    assert "kidney injury" not in html.lower()
    assert 'data-overview="rejected"' in html


# What every overview of Fatima's facts must name: her recorded allergy
# and the medicines on her latest prescription.
NAMED = (
    " An allergy to Penicillin is recorded, and the latest prescription"
    " lists Thyronorm and Uprise-D3."
)


class Replies:
    """A chat that answers from a list and keeps the messages it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []

    def __call__(self, messages, **_):
        self.sent.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_live_overviews_of_several_documents_pass():
    # qwen3.5:4b's replies over the synthetic patients' records, live.
    meera = facts_text(build_facts(inputs(*PATIENTS["meera"])))
    assert check(
        "The latest report from 2025-04-12 flags Haemoglobin 11.9 g/dL, MCV 81 fL"
        " and Serum Ferritin 9.8 ng/mL as low, while RDW-CV 16.2 % is flagged"
        " high. Diagnoses stated include Type 2 diabetes mellitus.",
        meera,
    ).follows_facts
    assert check(
        "The patient has 4 documents dated from 2024-09-05 to 2025-03-15. The"
        " latest report from 2025-01-08 flags Vitamin D (25-OH) at 18.4 ng/mL."
        " TSH is listed as falling based on 2 reports from 2024-09-05 and"
        " 2025-01-08. Diagnoses stated are Hypothyroidism from 2024-09-09 and"
        " 2025-03-15.",
        FATIMA_LINES,
    ).follows_facts


def test_a_value_beside_the_wrong_item_in_a_list_fails():
    meera = facts_text(build_facts(inputs(*PATIENTS["meera"])))
    # 33.2 is PCV's, 2024-06-14 isn't a date of Haemoglobin's flagged value.
    assert check("The latest report flags Haemoglobin 33.2 %.", meera).numbers == (
        "33.2",
    )
    assert not check(
        "Iron deficiency anaemia was stated on 2025-04-12.", meera
    ).follows_facts


def test_the_check_needs_the_facts_as_facts_text_writes_them():
    with pytest.raises(TypeError):
        check("TSH is falling.", str(FATIMA_LINES))


def test_a_rejected_overview_is_sent_back_once_with_what_failed():
    facts = build_facts(inputs(*PATIENTS["fatima"]))
    chat = Replies(
        "TSH is improving at 2.5." + NAMED,
        "TSH is falling, based on 2 reports." + NAMED,
    )

    overview = write_overview(facts, "p", chat=chat)

    assert (overview.outcome, overview.text, overview.attempts) == (
        "shown", "TSH is falling, based on 2 reports." + NAMED, 2
    )  # fmt: skip
    first, second = chat.sent
    assert second[:2] == first
    assert second[2] == {
        "role": "assistant",
        "content": "TSH is improving at 2.5." + NAMED,
    }
    assert second[3]["role"] == "user"
    assert "2.5" in second[3]["content"] and "improving" in second[3]["content"]


def test_an_overview_that_fails_twice_is_left_out():
    facts = build_facts(inputs(*PATIENTS["fatima"]))
    chat = Replies("TSH is improving.", "TSH is worsening.")

    overview = write_overview(facts, "p", chat=chat)

    assert (overview.outcome, overview.text, overview.attempts) == ("rejected", None, 2)
    assert overview.check.unknown_words == 1


@pytest.mark.parametrize("retry", [ConnectionError("down"), "  "])
def test_a_retry_with_no_answer_leaves_the_first_rejection(retry):
    facts = build_facts(inputs(*PATIENTS["fatima"]))

    overview = write_overview(facts, "p", chat=Replies("TSH is improving.", retry))

    assert (overview.outcome, overview.text) == ("rejected", None)
    assert overview.check.unknown_words == 1


def test_a_passing_overview_is_not_sent_again():
    facts = build_facts(inputs(*PATIENTS["fatima"]))
    chat = Replies("TSH is falling." + NAMED)

    overview = write_overview(facts, "p", chat=chat)

    assert (overview.outcome, overview.attempts, len(chat.sent)) == ("shown", 1, 1)


def test_a_passing_overview_is_shown_escaped():
    facts = build_facts(inputs(*PATIENTS["fatima"]))
    reply = "TSH is falling <b>" + NAMED
    overview = write_overview(facts, "p", chat=lambda *a, **k: reply)

    assert overview.outcome == "rejected"  # "b" is not a word of the facts, twice
    reply = "  TSH is\nfalling. " + NAMED
    overview = write_overview(facts, "p", chat=lambda *a, **k: reply)
    assert (overview.outcome, overview.text) == ("shown", "TSH is falling." + NAMED)
    html = summary_html(
        facts, Overview("TSH <i>falling</i>", "shown"), generated_at=NOW
    )
    assert "&lt;i&gt;falling&lt;/i&gt;" in html


def test_the_overview_reads_only_the_facts(model_client):
    facts = build_facts(inputs(*PATIENTS["fatima"]))
    model_client.replies[llm.PRIMARY_MODEL] = ["TSH is falling." + NAMED]

    overview = write_overview(facts, "the prompt")

    [call] = model_client.calls
    assert call["messages"] == [
        {"role": "system", "content": "the prompt"},
        {"role": "user", "content": facts_text(facts)},
    ]
    assert overview.outcome == "shown"


def test_no_model_answering_leaves_the_overview_out(model_client):
    facts = build_facts(inputs(*PATIENTS["fatima"]))
    model_client.replies[llm.PRIMARY_MODEL] = [ConnectionError("down")]

    overview = write_overview(facts, "p")

    assert overview.outcome == "no_model"


@pytest.mark.parametrize("failure", [ConnectionError("down"), ("cut off", "length")])
def test_the_fallback_model_never_writes_the_overview(model_client, failure):
    # The primary is down, or runs out of tokens: the fallback could
    # answer, but the overview is left out instead.
    facts = build_facts(inputs(*PATIENTS["fatima"]))
    model_client.replies[llm.PRIMARY_MODEL] = [failure]
    model_client.replies[llm.FALLBACK_MODEL] = ["TSH is falling." + NAMED]

    overview = write_overview(facts, "p")

    assert (overview.outcome, overview.text) == ("no_model", None)
    assert [c["model"] for c in model_client.calls] == [llm.PRIMARY_MODEL]
    html = summary_html(facts, overview, generated_at=NOW)
    assert 'data-overview="no_model"' in html and "TSH is falling" not in html


def test_the_retry_goes_to_the_primary_only(model_client):
    facts = build_facts(inputs(*PATIENTS["fatima"]))
    model_client.replies[llm.PRIMARY_MODEL] = [
        "TSH is improving." + NAMED,
        ConnectionError("down"),
    ]
    model_client.replies[llm.FALLBACK_MODEL] = ["TSH is falling." + NAMED]

    overview = write_overview(facts, "p")

    assert (overview.outcome, overview.text) == ("rejected", None)
    assert [c["model"] for c in model_client.calls] == [llm.PRIMARY_MODEL] * 2


def test_an_empty_reply_leaves_the_overview_out():
    facts = build_facts(inputs(*PATIENTS["fatima"]))
    assert write_overview(facts, "p", chat=lambda *a, **k: "  ").outcome == "empty"


# -------------------------------------------------------------------
# Escaping
# -------------------------------------------------------------------
def test_html_in_a_printed_value_is_escaped_in_the_summary():
    r = key("d09").model_copy(deep=True)
    r.observations[0].printed.value_text = "<script>x()</script>"
    r.remarks[0].printed.text = '<img src="http://evil.test/">'
    _, html, _ = page(inputs("d09", records={"d09": r}))
    assert "<script" not in html and "<img" not in html
    assert "&lt;script&gt;x()&lt;/script&gt;" in html


# -------------------------------------------------------------------
# A document printed for another patient (LC-13)
# -------------------------------------------------------------------
def another_patients_documents():
    """Fatima's reports and prescription, then Nandini's, uploaded to Fatima."""
    lc13 = [SignalHit(signal="LC-13", details={"field": "name"})]
    records = {"d20": marked("d20", lc13), "d21": marked("d21", lc13)}
    ins = inputs("d09", "d11", "d12")
    ins += inputs("d20", "d21", records=records, status="needs_review")
    return ins, records


def test_another_patients_values_all_await_review():
    ins, records = another_patients_documents()
    facts, _, soup = page(ins)

    [d20] = [d for d in facts.documents if d.document_id == "id-d20"]
    assert d20.in_doubt
    lab_facts = [v for s in facts.labs for v in s.values if v.doc is d20]
    assert lab_facts
    reason = SIGNALS["LC-13"].reason
    assert all(reason in [r.text for r in v.awaiting] for v in lab_facts)
    expected = sum(1 + len(list(r.items())) for r in records.values())
    assert facts.awaiting == expected
    for v in lab_facts:
        item = soup.find(
            "li", attrs={"data-item": v.item.id, "data-doc": f"D{d20.number}"}
        )
        assert "awaiting review" in item.get_text()


def test_another_patients_values_are_never_the_latest_or_in_a_trend():
    ins, _ = another_patients_documents()
    facts, _, _ = page(ins)

    tsh = series(facts, "tsh")
    assert tsh.latest.doc.document_id == "id-d11"
    assert all(
        v.doc.document_id in ("id-d09", "id-d11")
        for v in (tsh.trend.values if tsh.trend else [])
    )
    assert not [f for f in facts.attention if f.doc.in_doubt]
    assert facts.latest_prescription.doc.document_id == "id-d12"


def test_the_header_is_never_taken_from_another_patients_document():
    ins, _ = another_patients_documents()
    facts, _, soup = page(ins)

    assert facts.patient.doc.document_id == "id-d12"
    assert "Nandini" not in soup.select_one("dl").get_text()


def test_an_undated_or_incomplete_document_keeps_its_values():
    # Only a patient mismatch puts a document's values in doubt: a dropped
    # row or an undated document is shown at the document, and its other
    # values, each checked on its line, stay as they are.
    record = marked("d11", [SignalHit(signal="LC-17")])
    facts, _, _ = page(inputs("d09") + inputs("d11", records={"d11": record}))

    [d11] = [d for d in facts.documents if d.document_id == "id-d11"]
    assert d11.reasons and not d11.in_doubt
    assert series(facts, "tsh").latest.doc is d11


# -------------------------------------------------------------------
# One series is one test
# -------------------------------------------------------------------
def with_urine_creatinine(doc_id: str):
    """A later report whose first result is a bare "Creatinine" under a urine panel."""
    record = key(doc_id)
    o = record.observations[0]
    o.printed.test_name, o.printed.panel = "Creatinine", "URINE BIOCHEMISTRY"
    o.printed.value_text, o.printed.unit = "88", "mg/dL"
    o.printed.reference_ranges, o.printed.flag_printed = [], None
    o.derived = o.derived.model_copy(
        update={"analyte": "creatinine", "value_num": 88.0, "ranges": [],
                "flag_computed": None, "converted": None}
    )  # fmt: skip
    return record


def test_a_urine_test_never_shares_a_series_with_the_blood_test():
    # Serum creatinine 1.6 H, then a urine creatinine 88: not "rising",
    # and the flagged 1.6 stays the latest serum value in Needs attention.
    ins = inputs("d06", "d08", records={"d08": with_urine_creatinine("d08")})
    facts, _, _ = page(ins)

    keys = {s.key for s in facts.labs}
    assert {"analyte:creatinine", "analyte:creatinine|urine"} <= keys
    serum = series(facts, "creatinine")
    assert [v.item.printed.value_text for v in serum.values] == ["1.6"]
    assert serum.trend is None
    assert "1.6" in [f.item.printed.value_text for f in facts.attention]


def test_every_flagged_value_of_the_latest_report_needs_attention():
    # The same test printed twice on one report, both flagged.
    record = key("d06")
    first = next(o for o in record.observations if o.derived.analyte == "creatinine")
    twin = first.model_copy(deep=True)
    twin.id = "obs-99"
    record.observations.append(twin)
    facts, _, _ = page(inputs("d06", records={"d06": record}))

    ids = [f.item.id for f in facts.attention if f.item.derived.analyte == "creatinine"]
    assert ids == [first.id, "obs-99"]


# -------------------------------------------------------------------
# The overview check: words the facts know, used to say the opposite
# -------------------------------------------------------------------
ROHAN_LINES = facts_text(build_facts(inputs(*PATIENTS["rohan"])))


@pytest.mark.parametrize(
    "lines, text",
    [
        # A direction no trend has (creatinine has one value).
        ("rohan", "Serum Creatinine is rising."),
        # The opposite direction: TSH's trend falls.
        ("fatima", "TSH is rising."),
        # A flagged value called in range or normal.
        ("rohan", "Serum Creatinine 1.6 mg/dL is within range."),
        ("rohan", "Serum Creatinine 1.6 mg/dL is normal."),
        # A list the facts hold something of, called empty.
        ("fatima", "The patient has no allergies."),
        ("fatima", "No prescriptions are listed."),
        # A count in words that isn't the count.
        ("fatima", "Five documents are included."),
        # A flag said of a value that isn't flagged.
        ("fatima", "TSH is flagged."),
    ],
)
def test_an_overview_that_contradicts_the_facts_fails(lines, text):
    facts_lines = {"rohan": ROHAN_LINES, "fatima": FATIMA_LINES}[lines]
    assert not check(text, facts_lines).follows_facts


@pytest.mark.parametrize(
    "lines, text",
    [
        ("fatima", "TSH is falling."),
        ("fatima", "Four documents are included."),
        ("fatima", "Vitamin D (25-OH) 18.4 ng/mL is flagged low."),
        ("rohan", "No trends are listed."),
    ],
)
def test_the_same_words_pass_where_the_facts_say_them(lines, text):
    facts_lines = {"rohan": ROHAN_LINES, "fatima": FATIMA_LINES}[lines]
    assert check(text, facts_lines).follows_facts


def test_a_word_of_a_printed_diagnosis_is_known_only_beside_it():
    # "evaluated" is printed in "Chronic kidney disease, to be evaluated";
    # it can't be said of anything else.
    assert check(
        "Chronic kidney disease, to be evaluated, is stated.", ROHAN_LINES
    ).follows_facts
    assert not check("Serum Creatinine is to be evaluated.", ROHAN_LINES).follows_facts


# -------------------------------------------------------------------
# The overview check: what an overview must name
# -------------------------------------------------------------------
def test_an_overview_that_names_no_recorded_allergy_is_rejected():
    text = "TSH is falling. The latest prescription lists Thyronorm and Uprise-D3."
    result = check(text, FATIMA_LINES)

    assert result.follows_facts and not result.ok
    assert result.missing == ("the recorded allergy to Penicillin",)
    assert check(text + " An allergy to Penicillin is recorded.", FATIMA_LINES).ok


def test_an_overview_that_leaves_out_the_latest_prescription_is_rejected():
    text = "TSH is falling. An allergy to Penicillin is recorded."
    result = check(text, FATIMA_LINES)

    assert result.follows_facts and not result.ok
    assert result.missing == (
        "the medicine Thyronorm on the latest prescription",
        "the medicine Uprise-D3 on the latest prescription",
    )
    # Every medicine on it must be named, not just one.
    assert check(text + " It lists Thyronorm.", FATIMA_LINES).missing == (
        "the medicine Uprise-D3 on the latest prescription",
    )


def test_a_strength_in_a_medicine_name_is_not_needed_to_name_it():
    # Meera's latest prescription prints "Glycomet GP 1"; "Glycomet GP" names it.
    meera = facts_text(build_facts(inputs(*PATIENTS["meera"])))
    text = (
        "The latest prescription lists Glycomet GP, Telma 40 mg, Orofer XT,"
        " Pan 40 mg and Dolo 650 mg."
    )
    assert check(text, meera).missing == ()
    assert check(text.replace("Glycomet GP", "Glycomet"), meera).missing == (
        "the medicine Glycomet GP 1 on the latest prescription",
    )


@pytest.mark.parametrize("text", ["NKDA", "None known", "No known drug allergies"])
def test_no_known_allergies_need_not_be_named(text):
    # Meera's prescriptions record "NKDA"; a record of no allergy names
    # no substance, so the overview may leave it out.
    meera = facts_text(build_facts(inputs(*PATIENTS["meera"])))
    assert not [what for what, _ in meera.required if "allergy" in what]

    record = key("d12").model_copy(deep=True)
    [allergy] = record.allergies
    allergy.printed.text, allergy.printed.substance = text, None
    allergy.derived.no_known_allergies = True
    lines = facts_text(build_facts(inputs("d12", records={"d12": record})))
    assert not [what for what, _ in lines.required if "allergy" in what]


def test_an_allergy_with_no_substance_parsed_is_named_by_its_text():
    record = key("d07").model_copy(deep=True)
    [allergy] = record.allergies
    allergy.printed.substance = None  # "Sulfa drugs (rash)"
    lines = facts_text(build_facts(inputs("d07", records={"d07": record})))

    assert ("the recorded allergy to Sulfa drugs (rash)", ("sulfa", "rash")) in (
        lines.required
    )
    # "Not known" says nobody asked: it names no substance either.
    allergy.printed.text = "Not known"
    lines = facts_text(build_facts(inputs("d07", records={"d07": record})))
    assert not [what for what, _ in lines.required if "allergy" in what]


def test_what_is_missing_is_sent_back_on_the_retry():
    facts = build_facts(inputs(*PATIENTS["fatima"]))
    left_out = "TSH is falling. The latest prescription lists Thyronorm and Uprise-D3."
    chat = Replies(left_out, left_out)

    overview = write_overview(facts, "p", chat=chat)

    assert (overview.outcome, overview.text, overview.attempts) == ("rejected", None, 2)
    assert overview.check.missing_facts == 1
    retry = chat.sent[1][3]["content"]
    assert "the recorded allergy to Penicillin" in retry


def test_the_judged_overviews_pass_or_fail_as_the_judge_found():
    # The six overviews the check showed in the first live overview eval,
    # and the facts they were written from. The judge found one critical
    # omission: gemma4:e2b's fatima overview leaves out the recorded
    # penicillin allergy. Only that one may now be rejected, and only for it.
    from evals.overview import patient_facts

    packet = json.loads(JUDGED.read_text(encoding="utf-8"))
    outcomes = {}
    for item in packet["items"]:
        model, patient = item["id"].split("/")
        result = check(item["overview"], facts_text(patient_facts(patient)))
        outcomes[item["id"]] = (result.follows_facts, result.missing)

    assert outcomes.pop("gemma4:e2b/fatima") == (
        True,
        ("the recorded allergy to Penicillin",),
    )
    assert set(outcomes.values()) == {(True, ())}


# -------------------------------------------------------------------
# What the overview may not say, and which values a trend may join
# -------------------------------------------------------------------
def test_an_allergy_from_another_patients_document_is_not_in_the_overview():
    record = marked("d07", [SignalHit(signal="LC-13")])
    facts = build_facts(
        inputs("d09") + inputs("d07", records={"d07": record}, status="needs_review")
    )
    assert facts.allergies and facts.allergies[0].doc.in_doubt
    lines = facts_text(facts)
    assert not [what for what, _ in lines.required if "allergy" in what]
    assert "Sulfa" not in lines
    assert not check("An allergy to Sulfa drugs is recorded.", lines).ok
    # Nor do the facts say there is none: it awaits review.
    assert "Allergies recorded: none" not in lines
    assert not check("No allergies are recorded.", lines).ok


def test_the_overview_keeps_a_qualified_value_qualified():
    record = key("d09").model_copy(deep=True)
    o = next(o for o in record.observations if o.printed.flag_printed)
    name = o.printed.test_name
    for printed in ("< 1.6", "<= 1.6", "≤1.6"):
        o.printed.value_text = printed
        lines = facts_text(build_facts(inputs("d09", records={"d09": record})))
        assert not check(f"{name} is 1.6.", lines).follows_facts
        assert check(f"{name} {printed}.", lines).follows_facts
    assert not check(f"{name} < 1.6.", lines).follows_facts  # it is ≤


@pytest.mark.parametrize(
    "a, b, same",
    [
        ("SPOT URINE", "24 HOUR URINE", False),
        ("24 HR URINE", "24-hours Urine", True),
        ("URINE BIOCHEMISTRY", "Urine", True),
        ("SERUM", "CSF", False),
    ],
)
def test_a_collection_method_is_part_of_the_test(a, b, same):
    assert (_specimen(a) == _specimen(b)) is same


def test_values_of_a_test_with_a_unit_need_it_printed_for_a_trend():
    assert tsh_trend(tsh_records(2.0, 4.5, units=[None, None])) is None


def test_values_of_a_test_without_a_unit_can_have_a_trend():
    records = tsh_records(2.0, 4.5, units=[None, None])
    for r in records.values():
        for o in r.observations:
            if o.derived.analyte == "tsh":
                o.printed.test_name = "INR"
                o.derived = o.derived.model_copy(update={"analyte": None})
    facts = build_facts(inputs(*records, records=records))
    [inr] = [s for s in facts.labs if s.key == "name:inr"]
    assert inr.trend.direction == "rising"
