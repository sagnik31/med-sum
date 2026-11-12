"""
The patient summary from records (summary/, views/summary.py): the facts,
the overview check, and the page. One test per accuracy bug that the
model-written summary showed, plus the summary's rules.
"""

from datetime import date, datetime, timezone

import pytest
from bs4 import BeautifulSoup

import llm
from dataset_records import key
from pipeline import signals
from pipeline.signals import SIGNALS, SignalHit
from record.common import Provenance
from record.document import DateSource
from summary.facts import SummaryInput, build_facts, trend
from summary.overview import Overview, check, facts_text, write_overview
from views.summary import summary_html

NOW = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc)
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
# Section 6, bug 1: remarks attached to the wrong tests
# -------------------------------------------------------------------
def test_bug1_remarks_stay_on_what_they_are_printed_on():
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
# Bug 2: a diagnosis invented from a single value
# -------------------------------------------------------------------
def test_bug2_a_single_creatinine_never_becomes_a_diagnosis():
    facts, html, _ = page(inputs(*PATIENTS["rohan"]))

    assert "kidney injury" not in html.lower() and "AKI" not in html
    # The only kidney diagnosis is the one printed, verbatim.
    assert [c.item.printed.text for c in facts.conditions] == [
        "Hypertension", "Chronic kidney disease, to be evaluated",
    ]  # fmt: skip
    lines = facts_text(facts)
    assert not check("The raised creatinine indicates acute kidney injury.", lines).ok


# -------------------------------------------------------------------
# Bug 3: "worsening" from a single data point
# -------------------------------------------------------------------
def test_bug3_one_value_has_no_trend():
    facts, html, _ = page(inputs(*PATIENTS["rohan"]))

    creatinine = series(facts, "creatinine")
    assert len(creatinine.values) == 1 and creatinine.trend is None
    assert "data-trend" not in html
    assert not check("Creatinine is worsening.", facts_text(facts)).ok


# -------------------------------------------------------------------
# Bug 4: a confidence label from one report
# -------------------------------------------------------------------
def test_bug4_no_confidence_labels_only_counts_of_reports():
    facts, _, soup = page(inputs(*PATIENTS["fatima"]))

    tsh = soup.select_one('[data-series="analyte:tsh"]')
    trend_text = tsh.select_one("[data-trend]").get_text(" ", strip=True)
    assert trend_text == "falling, based on 2 reports (D1, D3) derived"
    assert "confidence" not in soup.get_text().lower()
    # Each value the trend counts is cited.
    assert len(tsh.select("cite")) == 2
    assert not check("TSH is falling with high confidence.", facts_text(facts)).ok


# -------------------------------------------------------------------
# Bug 5: upload order taken for the timeline
# -------------------------------------------------------------------
def test_bug5_documents_are_ordered_by_their_own_date_not_upload():
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
# Bug 6: tests filed under an organ system
# -------------------------------------------------------------------
def test_bug6_tests_stay_under_their_printed_panel():
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
# Bug 7: lipid status guessed from a statin
# -------------------------------------------------------------------
def test_bug7_a_statin_without_a_lipid_panel_gives_no_lipid_facts():
    facts, html, _ = page(inputs(*PATIENTS["rohan"]))
    lines = facts_text(facts)

    assert "Atorva" in lines  # the statin is listed as printed
    assert "lipid" not in lines.lower() and "cholesterol" not in lines.lower()
    assert "lipid" not in html.lower() and "cholesterol" not in html.lower()
    assert not check("Lipids are controlled on Atorva.", lines).ok


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
    assert soup.select_one('[data-warning="awaiting"]').get_text().startswith("1 ")
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


def test_every_value_cites_its_document_page_and_lines():
    facts, _, soup = page(inputs(*PATIENTS["fatima"]))
    cites = soup.select("cite")
    assert cites
    for c in cites:
        n = int(c["data-doc"][1:])
        record = facts.documents[n - 1].record
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
    ],
)
def test_an_overview_made_of_the_facts_passes(text):
    assert check(text, FATIMA_LINES).ok


def test_a_live_overview_of_one_report_passes():
    # qwen3.5:4b's reply for d09 alone, on a live run.
    lines = facts_text(build_facts(inputs("d09")))
    reply = (
        "The patient has one lab report dated 2024-09-05. The latest report flags "
        "TSH at 8.9 µIU/mL as high. No trends are listed. No prescriptions or "
        "diagnoses are stated."
    )
    assert check(reply, lines).ok


@pytest.mark.parametrize(
    "text, numbers, words",
    [
        ("TSH fell to 2.5.", 1, 0),  # a number not in the facts
        ("The latest prescription lists Eltroxin.", 0, 1),  # a medicine
        # A test. ("4" is a number of the facts: numbers are checked as a set.)
        ("Free T4 was not tested.", 0, 3),
        ("Graves disease is stated.", 0, 2),  # a diagnosis
        ("TSH is improving.", 0, 1),  # a judgement
        ("Her thyroid is well controlled.", 0, 4),
    ],
)
def test_an_overview_with_anything_outside_the_facts_fails(text, numbers, words):
    result = check(text, FATIMA_LINES)
    assert not result.ok
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


def test_a_passing_overview_is_shown_escaped():
    facts = build_facts(inputs(*PATIENTS["fatima"]))
    overview = write_overview(facts, "p", chat=lambda *a, **k: "TSH is falling <b>")

    assert overview.outcome == "rejected"  # "b" is not a word of the facts
    overview = write_overview(facts, "p", chat=lambda *a, **k: "  TSH is\nfalling. ")
    assert (overview.outcome, overview.text) == ("shown", "TSH is falling.")
    html = summary_html(
        facts, Overview("TSH <i>falling</i>", "shown"), generated_at=NOW
    )
    assert "&lt;i&gt;falling&lt;/i&gt;" in html


def test_the_overview_reads_only_the_facts(model_client):
    facts = build_facts(inputs(*PATIENTS["fatima"]))
    model_client.replies[llm.PRIMARY_MODEL] = ["TSH is falling."]

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
    model_client.replies[llm.FALLBACK_MODEL] = [ConnectionError("down")]

    overview = write_overview(facts, "p")

    assert overview.outcome == "no_model"


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
