"""The document view built by code from a record (views/document.py)."""

import re

import pytest
from bs4 import BeautifulSoup, Doctype

from dataset_records import DOCS, key
from pipeline import signals
from pipeline.signals import SIGNALS, SignalHit
from record.common import ItemStatus, Provenance
from record.document import DateSource, DocumentRecord
from record.printed import DocumentType
from views.document import TEXT, document_html
from views.html import num
from views.reasons import FIELD_LABELS, plain

ALL_IDS = sorted(DOCS)
PUNCTUATION = re.compile(r"^[\s():;,.\-·]*$")
# Judgements the view must never add on its own.
JUDGEMENTS = ("abnormal", "critical", "severe", "worsening", "improving",
              "concerning", "dangerous", "deranged", "elevated")  # fmt: skip


def view(record: DocumentRecord) -> str:
    return document_html(record, parsed=record.document.type is not DocumentType.OTHER)


def stamped(record: DocumentRecord) -> DocumentRecord:
    record = record.model_copy(deep=True)
    for holder in [record.document, record.patient, *record.items()]:
        if holder is not None:
            holder.provenance = Provenance(run_id="r", node="parse")
    return record


def record_strings(record: DocumentRecord) -> set[str]:
    """Every string and number in the record, as the view would print it."""
    out: set[str] = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, bool) or v is None:
            return
        elif isinstance(v, (int, float)):
            out.add(num(v))
        else:
            out.add(str(v))

    walk(record.model_dump(mode="json"))
    return out


def text_nodes(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["style", "title"]):
        tag.decompose()
    return [
        s.strip()
        for s in soup.find_all(string=True)
        if not isinstance(s, Doctype) and not PUNCTUATION.match(s)
    ]


def view_words() -> set[str]:
    return (
        set(TEXT.values())
        | {s.reason for s in SIGNALS.values()}
        | set(FIELD_LABELS.values())
    )


# -------------------------------------------------------------------
# Every record item appears
# -------------------------------------------------------------------
@pytest.mark.parametrize("doc_id", ALL_IDS)
def test_every_item_of_the_record_appears(doc_id):
    record = key(doc_id)
    html = view(record)

    if record.document.type is DocumentType.OTHER:
        for p in record.pages:
            for line in p.lines:
                if line.strip():
                    assert BeautifulSoup(f"<p>{line}</p>", "html.parser").text in (
                        BeautifulSoup(html, "html.parser").text
                    )
        return
    nodes = set(text_nodes(html))
    for item in record.items():
        assert f'data-item="{item.id}"' in html, item.id
    for o in record.observations:
        assert {o.printed.test_name, o.printed.value_text} <= nodes, o.id
        assert set(o.printed.reference_ranges) <= nodes, o.id
        for v in (o.printed.unit, o.printed.flag_printed, o.printed.panel):
            assert v is None or v in nodes, o.id
    for m in record.medications:
        p = m.printed
        values = (p.form, p.name, p.strength, p.dose, p.frequency, p.route,
                  p.duration, p.instructions)  # fmt: skip
        assert {v for v in values if v is not None} <= nodes, m.id
    for item in [*record.conditions, *record.remarks, *record.allergies]:
        assert item.printed.text in nodes, item.id
    for v in record.vitals:
        assert v.printed.value_text in nodes and v.printed.name in nodes, v.id
    if record.patient is not None:
        p = record.patient.printed
        for value in (p.name, p.age, p.sex, p.date_of_birth, p.patient_id):
            assert value is None or value.text in nodes
    d = record.document.printed
    for value in (d.facility, *d.clinicians, d.report_date, d.collection_date,
                  d.prescription_date):  # fmt: skip
        assert value is None or value.text in nodes


# -------------------------------------------------------------------
# Nothing outside the record
# -------------------------------------------------------------------
@pytest.mark.parametrize("doc_id", ALL_IDS)
def test_every_word_on_the_page_is_the_record_s_or_the_view_s_own(doc_id):
    record = key(doc_id)
    allowed = record_strings(record) | view_words()

    strays = [t for t in text_nodes(view(record)) if t not in allowed]

    assert strays == []


@pytest.mark.parametrize("doc_id", ALL_IDS)
def test_no_judgement_the_record_does_not_hold(doc_id):
    record = key(doc_id)
    printed = " ".join(record_strings(record)).lower()
    page = BeautifulSoup(view(record), "html.parser").text.lower()

    for word in JUDGEMENTS:
        if word not in printed:
            assert word not in page, word


def test_the_view_s_own_words_hold_no_judgement():
    words = " ".join(view_words()).lower()
    assert [w for w in JUDGEMENTS if w in words] == []


def test_a_computed_flag_is_shown_only_as_derived():
    record = key("d03")  # Haemoglobin 11.2 L, computed low
    html = view(record)
    soup = BeautifulSoup(html, "html.parser")
    row = soup.find(attrs={"data-item": "obs-1"})
    derived = [s.get_text(" ", strip=True) for s in row.select(".derived")]

    assert "low derived" in derived
    # The printed flag is in its own cell, as printed.
    assert "L" in [td.get_text(strip=True) for td in row.find_all("td")]


def test_derived_values_are_marked():
    record = key("d02")  # 1-0-1 for 30 days: twice a day, 30 days
    soup = BeautifulSoup(view(record), "html.parser")
    row = soup.find(attrs={"data-item": "med-1"})

    [derived] = row.select(".derived")
    assert derived.get_text(" ", strip=True) == "2 times a day ; 30 days derived"
    date = soup.find(string="Document date").find_next("dd")
    assert date.select_one(".tag").get_text() == "derived"


def test_an_undated_document_says_so_and_invents_no_date():
    record = key("d02").model_copy(deep=True)
    record.document.printed.prescription_date = None
    record.document.derived = record.document.derived.model_copy(
        update={
            "prescription_date": None,
            "document_date": None,
            "date_source": DateSource.UNDATED,
        }
    )
    soup = BeautifulSoup(view(record), "html.parser")
    date = soup.find(string="Document date").find_next("dd")
    assert date.get_text(" ", strip=True) == "undated derived"


# -------------------------------------------------------------------
# Flagged items show their reasons
# -------------------------------------------------------------------
def test_a_flagged_item_shows_its_reasons_in_plain_words():
    record = stamped(key("d09"))
    hits = [
        SignalHit(signal="LC-10", item_id="obs-2", field="flag_printed"),
        SignalHit(signal="LC-03", item_id="obs-2", field="value_text"),
        SignalHit(signal="LC-12"),
    ]
    signals.apply(record, hits)

    html = view(record)
    soup = BeautifulSoup(html, "html.parser")
    row = soup.find(attrs={"data-item": "obs-2"})
    reasons = row.find_next_sibling("tr")

    assert "review" in row["class"]
    assert reasons.select_one("strong").get_text() == "Awaiting review"
    shown = [li.get_text(" ", strip=True) for li in reasons.select("li")]
    assert shown == [
        "printed flag : " + SIGNALS["LC-10"].reason,
        "value : " + SIGNALS["LC-03"].reason,
    ]
    # The document-level reason is in the banner; no signal ids are shown.
    banner = soup.find(attrs={"data-item": "review"})
    assert SIGNALS["LC-12"].reason in banner.get_text()
    assert "LC-" not in soup.get_text()
    # Unflagged items carry no review mark.
    ok = soup.find(attrs={"data-item": "obs-1"})
    assert "review" not in (ok.get("class") or [])


def test_a_flagged_header_value_shows_its_reason():
    record = stamped(key("d09"))
    signals.apply(record, [SignalHit(signal="LC-13", item_id="patient", field="name")])
    assert record.patient.provenance.status is ItemStatus.LOW_CONFIDENCE

    soup = BeautifulSoup(view(record), "html.parser")
    patient = soup.find(attrs={"data-item": "patient"})
    assert patient.select_one("li").get_text(" ", strip=True) == (
        "name : " + SIGNALS["LC-13"].reason
    )


def test_a_clean_record_has_no_review_banner():
    record = stamped(key("d09"))
    signals.apply(record, [])
    html = view(record)
    assert 'data-item="review"' not in html and "Awaiting review" not in html


def test_plain_reasons_drop_the_signal_id():
    r = plain(SignalHit(signal="LC-04", item_id="obs-1", field="value_text").reason)
    assert (r.text, r.field) == (SIGNALS["LC-04"].reason, "value")
    assert plain("LC-12: x").field is None
    r = plain("LC-11 [clinicians[1]]: anything")
    assert (r.text, r.field) == (SIGNALS["LC-11"].reason, "clinician")
    assert plain("not a signal").text == "not a signal"


# -------------------------------------------------------------------
# HTML in a printed value is escaped
# -------------------------------------------------------------------
def test_html_in_a_printed_value_is_escaped():
    record = key("d09").model_copy(deep=True)
    o = record.observations[0]
    o.printed.test_name = '<script>alert("x")</script>'
    o.printed.value_text = '<img src="http://evil.test/a.png" onerror=1>'
    record.remarks[0].printed.text = "<b>bold</b> & <i>it</i>"
    record.document.printed.facility.text = '"><iframe src=x>'

    html = view(record)

    assert "<script" not in html and "<img" not in html and "<iframe" not in html
    assert "<b>bold" not in html
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
    soup = BeautifulSoup(html, "html.parser")
    assert soup.find(string='<script>alert("x")</script>') is not None
    assert soup.find(string="<b>bold</b> & <i>it</i>") is not None


def test_html_in_an_item_id_is_escaped_in_its_attribute():
    record = key("d09").model_copy(deep=True)
    record.observations[0].id = 'x" onclick="alert(1)'
    html = view(record)
    assert 'onclick="alert' not in html
    assert 'data-item="x&quot; onclick=&quot;alert(1)"' in html


# -------------------------------------------------------------------
# Documents with nothing parsed
# -------------------------------------------------------------------
def test_an_unparsed_record_shows_its_pages():
    record = key("d17")
    html = document_html(record, parsed=False)
    assert TEXT["not_parsed"] in html
    assert TEXT["other"] in html
    assert "Lab results" not in html
