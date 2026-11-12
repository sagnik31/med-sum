"""
The document view: one document's record as HTML, built by code.

What it shows, and what it never does:
- every item of the record, each value exactly as printed (escaped);
- values code computed from the printed text (the document date, a flag
  from the printed range, a unit conversion, a dose count) next to them,
  each marked "derived";
- for an item, header value or document that awaits a clinician's review,
  the reasons in plain words, from the signal table;
- no judgement the record doesn't hold: no "abnormal", no severity, no
  interpretation. A flag is shown only as printed, or as computed from the
  printed range and marked derived.

A record with no parsed items (a document of a type that isn't parsed, or
one whose every parse failed) shows its page transcriptions instead, so
the reviewer still sees what was read.

The view's own wording is all in TEXT, so a test can check that every
other word on the page comes from the record.
"""

from record.common import ItemStatus, Provenance
from record.document import (
    DocumentRecord,
    MedicationRequest,
    Observation,
    RecordItem,
)
from record.printed import DocumentType, PrintedText, RemarkScope
from views.html import esc, num, page
from views.reasons import plain

TEXT = {
    "title": "Document",
    "lab_report": "Lab report",
    "prescription": "Prescription",
    "other": "Document of an unsupported type",
    "discharge_summary": "Discharge summary",
    "imaging_report": "Imaging report",
    "derived": "derived",
    "awaiting": "Awaiting review",
    "banner": "Some values in this document await review by a clinician.",
    "banner_document": "About the whole document:",
    "patient": "Patient (as printed)",
    "name": "Name",
    "age": "Age",
    "sex": "Sex",
    "date_of_birth": "Date of birth",
    "patient_id": "Patient ID",
    "details": "Document details",
    "facility": "Facility",
    "clinician": "Clinician",
    "report_date": "Report date",
    "collection_date": "Collection date",
    "prescription_date": "Prescription date",
    "document_date": "Document date",
    "undated": "undated",
    "from_report_date": "from the report date",
    "from_collection_date": "from the collection date",
    "from_prescription_date": "from the prescription date",
    "results": "Lab results",
    "no_panel": "Results",
    "test": "Test",
    "result": "Result",
    "unit": "Unit",
    "range": "Reference range",
    "flag": "Flag (printed)",
    "computed": "Computed from the range",
    "converted": "Converted",
    "converted_from": "from",
    "medicines": "Medicines",
    "medicine": "Medicine",
    "form": "Form",
    "strength": "Strength",
    "dose": "Dose",
    "frequency": "Frequency",
    "route": "Route",
    "duration": "Duration",
    "instructions": "Instructions",
    "times_a_day": "times a day",
    "as_needed": "as needed",
    "days": "days",
    "conditions": "Stated diagnoses and history",
    "diagnosis": "Diagnosis",
    "provisional_diagnosis": "Provisional diagnosis",
    "impression": "Impression",
    "history": "History",
    "remarks": "Remarks and advice",
    "advice": "Advice",
    "comment": "Comment",
    "on_test": "on the test",
    "on_panel": "on the panel",
    "on_document": "on the document",
    "vitals": "Vitals",
    "vital": "Vital",
    "value": "Value",
    "allergies": "Allergies",
    "no_known_allergies": "no known allergies",
    "procedures": "Procedures",
    "encounters": "Encounters",
    "imaging": "Imaging findings",
    "transcription": "Transcription",
    "not_parsed": "No values were extracted from this document; "
    "its pages are shown as they were read.",
    "page": "Page",
    "footer": "Values are shown exactly as printed on the document. Values "
    "marked derived were computed by code from the printed text. "
    "This page is not medical advice.",
}


def _t(key: str) -> str:
    return esc(TEXT[key])


def _v(value) -> str:
    """A record value in an element of its own."""
    return f'<span class="v">{esc(value)}</span>'


def _derived(inner: str) -> str:
    return (
        f'<span class="derived">{inner} <span class="tag">{_t("derived")}</span></span>'
    )


def _awaits(provenance: Provenance | None) -> bool:
    return provenance is not None and provenance.status is ItemStatus.LOW_CONFIDENCE


def _reasons(provenance: Provenance | None) -> str:
    """The reasons an item awaits review, as a list; '' when it doesn't."""
    if not _awaits(provenance):
        return ""
    items = []
    for reason in provenance.low_confidence_reasons:
        r = plain(reason)
        about = f"<em>{esc(r.field)}</em>: " if r.field else ""
        items.append(f"<li>{about}<span>{esc(r.text)}</span></li>")
    return (
        f'<div class="reasons-block"><strong>{_t("awaiting")}</strong>'
        f'<ul class="reasons">{"".join(items)}</ul></div>'
    )


def _row_attrs(item: RecordItem) -> str:
    cls = ' class="review"' if _awaits(item.provenance) else ""
    return f' data-item="{esc(item.id)}"{cls}'


def _cell(value: str | None) -> str:
    return f"<td>{esc(value)}</td>" if value is not None else "<td></td>"


def _dl_row(label_key: str, value: PrintedText | None) -> str:
    if value is None:
        return ""
    return f"<dt>{_t(label_key)}</dt><dd>{esc(value.text)}</dd>"


# -------------------------------------------------------------------
# Sections
# -------------------------------------------------------------------
def _patient(record: DocumentRecord) -> str:
    if record.patient is None:
        return ""
    p = record.patient.printed
    rows = "".join(
        _dl_row(k, getattr(p, k))
        for k in ("name", "age", "sex", "date_of_birth", "patient_id")
    )
    if not rows:
        return ""
    cls = ' class="review"' if _awaits(record.patient.provenance) else ""
    return (
        f'<section data-item="patient"{cls}><h2>{_t("patient")}</h2>'
        f"<dl>{rows}</dl>{_reasons(record.patient.provenance)}</section>"
    )


def _details(record: DocumentRecord) -> str:
    d = record.document
    rows = [_dl_row("facility", d.printed.facility)]
    rows += [_dl_row("clinician", c) for c in d.printed.clinicians]
    rows += [
        _dl_row(k, getattr(d.printed, k))
        for k in ("report_date", "collection_date", "prescription_date")
    ]
    dates = d.derived
    if dates.document_date is not None:
        shown = (
            f"{_v(dates.document_date.isoformat())} "
            f'<span class="muted">{_t("from_" + dates.date_source.value)}</span>'
        )
    else:
        shown = _t("undated")
    rows.append(f"<dt>{_t('document_date')}</dt><dd>{_derived(shown)}</dd>")
    return f'<section data-item="document"><h2>{_t("details")}</h2><dl>{"".join(rows)}</dl></section>'


def _observation_rows(o: Observation) -> str:
    p, dv = o.printed, o.derived
    computed = (
        _derived(esc(dv.flag_computed.value)) if dv.flag_computed is not None else ""
    )
    converted = ""
    if dv.converted is not None:
        c = dv.converted
        converted = _derived(
            f"{_v(num(c.value))} {_v(c.unit)} "
            f'<span class="muted">{_t("converted_from")}</span> {_v(c.from_unit)}'
        )
    ranges = "<br>".join(esc(r) for r in p.reference_ranges)
    row = (
        f"<tr{_row_attrs(o)}>"
        f"<td>{esc(p.test_name)}</td><td>{esc(p.value_text)}</td>{_cell(p.unit)}"
        f"<td>{ranges}</td>{_cell(p.flag_printed)}<td>{computed}</td><td>{converted}</td>"
        "</tr>"
    )
    reasons = _reasons(o.provenance)
    if reasons:
        row += f'<tr class="review"><td colspan="7">{reasons}</td></tr>'
    return row


def _observations(record: DocumentRecord) -> str:
    if not record.observations:
        return ""
    panels: dict[str | None, list[Observation]] = {}
    for o in record.observations:
        panels.setdefault(o.printed.panel, []).append(o)
    head = "".join(
        f"<th>{_t(k)}</th>"
        for k in ("test", "result", "unit", "range", "flag", "computed", "converted")
    )
    parts = [f"<section><h2>{_t('results')}</h2>"]
    for panel, rows in panels.items():
        title = esc(panel) if panel is not None else _t("no_panel")
        body = "".join(_observation_rows(o) for o in rows)
        parts.append(
            f"<h3>{title}</h3><table><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )
    parts.append("</section>")
    return "".join(parts)


def _schedule(m: MedicationRequest) -> str:
    bits = []
    s = m.derived.schedule
    if s is not None and s.times_per_day is not None:
        bits.append(f"{_v(num(s.times_per_day))} <span>{_t('times_a_day')}</span>")
    if s is not None and s.as_needed:
        bits.append(f"<span>{_t('as_needed')}</span>")
    if m.derived.duration_days is not None:
        bits.append(f"{_v(num(m.derived.duration_days))} <span>{_t('days')}</span>")
    return _derived("; ".join(bits)) if bits else ""


def _medications(record: DocumentRecord) -> str:
    if not record.medications:
        return ""
    keys = ("form", "medicine", "strength", "dose", "frequency", "route",
            "duration", "instructions")  # fmt: skip
    head = "".join(f"<th>{_t(k)}</th>" for k in keys) + f"<th>{_t('derived')}</th>"
    rows = []
    for m in record.medications:
        p = m.printed
        cells = "".join(
            _cell(v)
            for v in (
                p.form,
                p.name,
                p.strength,
                p.dose,
                p.frequency,
                p.route,
                p.duration,
                p.instructions,
            )  # fmt: skip
        )
        rows.append(f"<tr{_row_attrs(m)}>{cells}<td>{_schedule(m)}</td></tr>")
        reasons = _reasons(m.provenance)
        if reasons:
            rows.append(f'<tr class="review"><td colspan="9">{reasons}</td></tr>')
    return (
        f"<section><h2>{_t('medicines')}</h2><table><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _list_section(title_key: str, entries: list[str]) -> str:
    if not entries:
        return ""
    return f"<section><h2>{_t(title_key)}</h2><ul>{''.join(entries)}</ul></section>"


def _li(item: RecordItem, inner: str) -> str:
    return f"<li{_row_attrs(item)}>{inner}{_reasons(item.provenance)}</li>"


def _conditions(record: DocumentRecord) -> str:
    return _list_section(
        "conditions",
        [
            _li(c, f"<strong>{_t(c.printed.kind.value)}</strong>: {_v(c.printed.text)}")
            for c in record.conditions
        ],
    )


def _remarks(record: DocumentRecord) -> str:
    entries = []
    for r in record.remarks:
        p = r.printed
        if p.scope is RemarkScope.TEST:
            where = f'<span class="muted">{_t("on_test")}</span> {_v(p.test_name)}'
        elif p.scope is RemarkScope.PANEL:
            where = f'<span class="muted">{_t("on_panel")}</span> {_v(p.panel)}'
        else:
            where = f'<span class="muted">{_t("on_document")}</span>'
        entries.append(
            _li(r, f"<strong>{_t(p.kind.value)}</strong> ({where}): {_v(p.text)}")
        )
    return _list_section("remarks", entries)


def _vitals(record: DocumentRecord) -> str:
    if not record.vitals:
        return ""
    head = "".join(f"<th>{_t(k)}</th>" for k in ("vital", "value", "unit"))
    rows = []
    for v in record.vitals:
        p = v.printed
        rows.append(
            f"<tr{_row_attrs(v)}><td>{esc(p.name)}</td><td>{esc(p.value_text)}</td>"
            f"{_cell(p.unit)}</tr>"
        )
        reasons = _reasons(v.provenance)
        if reasons:
            rows.append(f'<tr class="review"><td colspan="3">{reasons}</td></tr>')
    return (
        f"<section><h2>{_t('vitals')}</h2><table><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _allergies(record: DocumentRecord) -> str:
    entries = []
    for a in record.allergies:
        p = a.printed
        extra = [_v(x) for x in (p.substance, p.reaction) if x is not None]
        inner = _v(p.text)
        if extra:
            inner += f' <span class="muted">({", ".join(extra)})</span>'
        if a.derived.no_known_allergies:
            inner += " " + _derived(_t("no_known_allergies"))
        entries.append(_li(a, inner))
    return _list_section("allergies", entries)


def _others(record: DocumentRecord) -> str:
    procedures = [
        _li(p, _v(p.printed.name)
            + (f" ({_v(p.printed.date.text)})" if p.printed.date else ""))
        for p in record.procedures
    ]  # fmt: skip
    encounters = [
        _li(e, _v(e.printed.kind)
            + (f" ({_v(e.printed.date.text)})" if e.printed.date else ""))
        for e in record.encounters
    ]  # fmt: skip
    imaging = [
        _li(i, " ".join(_v(x) for x in (i.printed.modality, i.printed.body_site,
                                         i.printed.finding, i.printed.impression)
                        if x is not None))
        for i in record.imaging_findings
    ]  # fmt: skip
    return (
        _list_section("procedures", procedures)
        + _list_section("encounters", encounters)
        + _list_section("imaging", imaging)
    )


def _transcription(record: DocumentRecord) -> str:
    parts = [f"<section><h2>{_t('transcription')}</h2><p>{_t('not_parsed')}</p>"]
    for p in record.pages:
        lines = "".join(f"<div>{esc(line)}</div>" for line in p.lines)
        parts.append(
            f'<h3>{_t("page")} {_v(num(p.page))}</h3><div class="lines">{lines}</div>'
        )
    parts.append("</section>")
    return "".join(parts)


def _banner(record: DocumentRecord) -> str:
    holders = [record.document.provenance]
    if record.patient is not None:
        holders.append(record.patient.provenance)
    holders += [i.provenance for i in record.items()]
    if not any(_awaits(p) for p in holders):
        return ""
    document = _reasons(record.document.provenance)
    if document:
        document = f"<p>{_t('banner_document')}</p>{document}"
    return (
        f'<div class="banner" data-item="review"><p>{_t("banner")}</p>{document}</div>'
    )


def document_html(record: DocumentRecord, *, parsed: bool = True) -> str:
    """
    The document's view as a whole HTML page. `parsed` is False when the
    record holds the page transcriptions only (type other, or no parse
    succeeded); the pages are shown then.
    """
    kind = record.document.type
    title = TEXT.get(kind.value, TEXT["title"])
    body = [
        f"<h1>{esc(title)}</h1>",
        _banner(record),
        _patient(record),
        _details(record),
    ]
    if parsed and kind is not DocumentType.OTHER:
        body += [
            _observations(record),
            _medications(record),
            _conditions(record),
            _remarks(record),
            _vitals(record),
            _allergies(record),
            _others(record),
        ]
    else:
        body.append(_transcription(record))
    body.append(f"<footer>{_t('footer')}</footer>")
    return page(title, "\n".join(b for b in body if b))
