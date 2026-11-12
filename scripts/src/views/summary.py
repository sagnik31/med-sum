"""
The patient summary page, built by code from the computed facts
(summary/facts.py). Only the overview is model-written, and it is shown
only when it passed its check (summary/overview.py); it is escaped like
every other value.

Layout, for a doctor's quick read before a visit:
  header      patient as printed, documents included, dates, warnings
  attention   flagged values in the latest report of each test; allergies
  medicines   on the latest prescription, then earlier prescriptions
  diagnoses   stated diagnoses, impressions and history, verbatim
  over time   lab results and vitals by date, with rising / falling only
  remarks     clinicians' remarks and advice, with what they are on
  timeline    every document in date order, with its review state
  overview    the checked model-written sentences
  footer      what was left out, and that this is not medical advice

Every value cites where it is printed: document number (D1, D2, ... in
date order, as the timeline lists them), page and lines.
"""

from datetime import datetime

from summary.facts import (
    DocEntry,
    Fact,
    Facts,
    Prescription,
    Series,
    medicine_line,
    remark_target,
)
from summary.overview import Overview
from views.html import esc, page

TEXT = {
    "title": "Patient summary",
    "patient": "Patient (as printed on",
    "documents": "Documents included",
    "span": "Dated",
    "to": "to",
    "generated": "Generated",
    "warnings": "Warnings",
    "undated_warning": "is undated: listed last and not used for trends or for the latest values.",
    "awaiting_warning": "values in these documents await review by a clinician; each is marked below.",
    "not_included": "documents are not included: still processing, failed, or not processed yet.",
    "not_parsed": "is of a type that isn't parsed; only its place in the timeline is shown.",
    "doc_reasons": "about the whole document:",
    "attention": "Needs attention",
    "attention_note": "Values with a printed flag, or outside their printed range, in the latest report of each test.",
    "no_attention": "No value in the latest report of any test has a printed flag or lies outside its printed range.",
    "allergies": "Allergies recorded",
    "no_allergies": "No allergy statement on any document.",
    "medicines": "Medicines",
    "latest_rx": "On the latest prescription",
    "earlier_rx": "Earlier prescriptions",
    "no_rx": "No prescription among the documents.",
    "conditions": "Stated diagnoses and impressions",
    "no_conditions": "No diagnosis, impression or history is printed on any document.",
    "labs": "Lab results over time",
    "vitals": "Vitals over time",
    "results": "Results",
    "test": "Test",
    "vital": "Vital",
    "values": "Values by date",
    "range": "Reference range (latest)",
    "trend": "Trend",
    "value": "Value",
    "unit": "Unit",
    "flag": "Flag (printed)",
    "computed": "Computed from the range",
    "date": "Date",
    "source": "Source",
    "remarks": "Remarks and advice",
    "on_test": "on the test",
    "on_panel": "on the panel",
    "on_document": "on the document",
    "advice": "Advice",
    "comment": "Comment",
    "diagnosis": "Diagnosis",
    "provisional_diagnosis": "Provisional diagnosis",
    "impression": "Impression",
    "history": "History",
    "timeline": "Documents in date order",
    "lab_report": "Lab report",
    "prescription": "Prescription",
    "other": "Unsupported type",
    "verified": "verified",
    "needs_review": "awaiting review",
    "undated": "undated",
    "awaiting": "awaiting review",
    "rising": "rising",
    "falling": "falling",
    "based_on": "based on",
    "reports": "reports",
    "derived": "derived",
    "overview": "Overview",
    "overview_note": "Written by a language model from the facts above, and checked: it uses no number or term that isn't in them.",
    "overview_rejected": "The overview was left out: the model's text used numbers or words that aren't in the facts above.",
    "overview_no_model": "The overview was left out: no model answered.",
    "overview_empty": "The overview was left out: the model returned nothing.",
    "footer": "Built by code from each document's checked record. Values are as printed; values marked derived were computed by code from the printed text. Trends are only rising or falling, from two or more values in the same unit. This summary is not medical advice.",
}


def _t(key: str) -> str:
    return esc(TEXT[key])


def _v(value) -> str:
    return f'<span class="v">{esc(value)}</span>'


def _cite(f: Fact) -> str:
    s = f.source
    lines = (
        f"{s.first_line}"
        if s.first_line == s.last_line
        else f"{s.first_line}-{s.last_line}"
    )
    return (
        f'<cite data-doc="D{s.doc}" data-page="{s.page}" data-lines="{lines}">'
        f"D{s.doc} p{s.page}:{lines}</cite>"
    )


def _date(doc: DocEntry) -> str:
    return (
        _v(doc.date.isoformat())
        if doc.date is not None
        else f'<span class="muted">{_t("undated")}</span>'
    )


def _awaiting(f: Fact) -> str:
    if not f.awaiting:
        return ""
    items = "".join(
        f"<li>{f'<em>{esc(r.field)}</em>: ' if r.field else ''}<span>{esc(r.text)}</span></li>"
        for r in f.awaiting
    )
    return f' <span class="tag review">{_t("awaiting")}</span><ul class="reasons">{items}</ul>'


def _section(title_key: str, body: str) -> str:
    return f"<section><h2>{_t(title_key)}</h2>{body}</section>"


# -------------------------------------------------------------------
# Header and warnings
# -------------------------------------------------------------------
def _header(facts: Facts, generated_at: datetime) -> str:
    rows = []
    p = facts.patient
    if p is not None:
        details = [x for x in (p.name, p.age, p.sex, p.patient_id) if x is not None]
        rows.append(
            f"<dt>{_t('patient')} D{p.doc.number})</dt><dd>"
            + ", ".join(_v(x.text) for x in details)
            + "</dd>"
        )
    rows.append(f"<dt>{_t('documents')}</dt><dd>{_v(len(facts.documents))}</dd>")
    dated = facts.dated
    if dated:
        rows.append(
            f"<dt>{_t('span')}</dt><dd>{_v(dated[0].date.isoformat())} {_t('to')} "
            f"{_v(dated[-1].date.isoformat())}</dd>"
        )
    rows.append(
        f"<dt>{_t('generated')}</dt><dd>{_v(generated_at.strftime('%Y-%m-%d %H:%M'))}</dd>"
    )

    warnings = []
    for d in facts.undated:
        warnings.append(f"<li>D{d.number} {_t('undated_warning')}</li>")
    for d in facts.documents:
        if not d.parsed:
            warnings.append(f"<li>D{d.number} {_t('not_parsed')}</li>")
        if d.reasons:
            reasons = "; ".join(esc(r.text) for r in d.reasons)
            warnings.append(f"<li>D{d.number}, {_t('doc_reasons')} {reasons}</li>")
    if facts.awaiting:
        warnings.append(
            f'<li data-warning="awaiting">{_v(facts.awaiting)} {_t("awaiting_warning")}</li>'
        )
    if facts.not_included:
        warnings.append(
            f'<li data-warning="not_included">{_v(facts.not_included)} {_t("not_included")}</li>'
        )
    block = ""
    if warnings:
        block = f'<div class="banner" data-section="warnings"><strong>{_t("warnings")}</strong><ul>{"".join(warnings)}</ul></div>'
    return (
        f'<h1>{_t("title")}</h1><dl data-section="header">{"".join(rows)}</dl>{block}'
    )


# -------------------------------------------------------------------
# Sections
# -------------------------------------------------------------------
def _value_cells(f: Fact) -> str:
    o = f.item
    computed = ""
    if o.derived.flag_computed is not None:
        computed = f'<span class="derived">{_v(o.derived.flag_computed.value)} <span class="tag">{_t("derived")}</span></span>'
    ranges = "<br>".join(esc(r) for r in o.printed.reference_ranges)
    return (
        f"<td>{esc(o.printed.test_name)}{_awaiting(f)}</td><td>{esc(o.printed.value_text)}</td>"
        f"<td>{esc(o.printed.unit or '')}</td><td>{ranges}</td>"
        f"<td>{esc(o.printed.flag_printed or '')}</td><td>{computed}</td>"
        f"<td>{_date(f.doc)}</td><td>{_cite(f)}</td>"
    )


def _attention(facts: Facts) -> str:
    if facts.attention:
        head = "".join(
            f"<th>{_t(k)}</th>"
            for k in (
                "test",
                "value",
                "unit",
                "range",
                "flag",
                "computed",
                "date",
                "source",
            )
        )
        rows = "".join(
            f'<tr data-item="{esc(f.item.id)}" data-doc="D{f.doc.number}">{_value_cells(f)}</tr>'
            for f in facts.attention
        )
        body = f'<p class="muted">{_t("attention_note")}</p><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>'
    else:
        body = f"<p>{_t('no_attention')}</p>"
    if facts.allergies:
        items = "".join(
            f"<li>{_v(a.item.printed.text)} ({_date(a.doc)}, {_cite(a)}){_awaiting(a)}</li>"
            for a in facts.allergies
        )
        body += f'<h3>{_t("allergies")}</h3><ul data-section="allergies">{items}</ul>'
    else:
        body += f"<h3>{_t('allergies')}</h3><p>{_t('no_allergies')}</p>"
    return (
        f'<section data-section="attention"><h2>{_t("attention")}</h2>{body}</section>'
    )


def _prescription(rx: Prescription) -> str:
    clinicians = ", ".join(_v(c.text) for c in rx.clinicians)
    head = f"<p>D{rx.doc.number}, {_date(rx.doc)}{', ' + clinicians if clinicians else ''}</p>"
    items = "".join(
        f'<li data-item="{esc(m.item.id)}">{" ".join(_v(x) for x in medicine_line(m.item))} ({_cite(m)}){_awaiting(m)}</li>'
        for m in rx.medicines
    )
    return f"{head}<ul>{items}</ul>"


def _medicines(facts: Facts) -> str:
    if facts.latest_prescription is None and not facts.earlier_prescriptions:
        return _section("medicines", f"<p>{_t('no_rx')}</p>")
    body = ""
    if facts.latest_prescription is not None:
        body += f'<h3>{_t("latest_rx")}</h3><div data-section="latest_rx">{_prescription(facts.latest_prescription)}</div>'
    if facts.earlier_prescriptions:
        body += f'<h3>{_t("earlier_rx")}</h3><div data-section="earlier_rx">'
        body += (
            "".join(_prescription(p) for p in facts.earlier_prescriptions) + "</div>"
        )
    return (
        f'<section data-section="medicines"><h2>{_t("medicines")}</h2>{body}</section>'
    )


def _conditions(facts: Facts) -> str:
    if not facts.conditions:
        return _section("conditions", f"<p>{_t('no_conditions')}</p>")
    items = "".join(
        f'<li data-item="{esc(c.item.id)}"><strong>{_t(c.item.printed.kind.value)}</strong>: '
        f"{_v(c.item.printed.text)} ({_date(c.doc)}, {_cite(c)}){_awaiting(c)}</li>"
        for c in facts.conditions
    )
    return f'<section data-section="conditions"><h2>{_t("conditions")}</h2><ul>{items}</ul></section>'


def _trend(s: Series) -> str:
    if s.trend is None:
        return ""
    docs = ", ".join(f"D{v.doc.number}" for v in s.trend.values)
    return (
        f'<span class="derived" data-trend="{s.trend.direction}">{_t(s.trend.direction)}, '
        f"{_t('based_on')} {_v(len(s.trend.values))} {_t('reports')} ({docs}) "
        f'<span class="tag">{_t("derived")}</span></span>'
    )


def _series_values(s: Series) -> str:
    parts = []
    for f in s.values:
        p = f.item.printed
        unit = f" {_v(p.unit)}" if p.unit else ""
        flag = f" {_v(p.flag_printed)}" if getattr(p, "flag_printed", None) else ""
        parts.append(
            f'<li data-item="{esc(f.item.id)}" data-doc="D{f.doc.number}">{_date(f.doc)}: {_v(p.value_text)}{unit}{flag} ({_cite(f)}){_awaiting(f)}</li>'
        )
    return f"<ul>{''.join(parts)}</ul>"


def _labs(facts: Facts) -> str:
    if not facts.labs:
        return ""
    panels: dict[str | None, list[Series]] = {}
    for s in facts.labs:
        panels.setdefault(s.panel, []).append(s)
    head = "".join(f"<th>{_t(k)}</th>" for k in ("test", "values", "range", "trend"))
    body = ""
    for panel, series in panels.items():
        rows = ""
        for s in series:
            latest = s.latest or s.values[-1]
            ranges = "<br>".join(esc(r) for r in latest.item.printed.reference_ranges)
            rows += (
                f'<tr data-series="{esc(s.key)}"><td>{esc(s.name)}</td><td>{_series_values(s)}</td>'
                f"<td>{ranges}</td><td>{_trend(s)}</td></tr>"
            )
        title = esc(panel) if panel is not None else _t("results")
        body += f"<h3>{title}</h3><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
    return f'<section data-section="labs"><h2>{_t("labs")}</h2>{body}</section>'


def _vitals(facts: Facts) -> str:
    if not facts.vitals:
        return ""
    head = "".join(f"<th>{_t(k)}</th>" for k in ("vital", "values"))
    rows = "".join(
        f'<tr data-series="{esc(s.key)}"><td>{esc(s.name)}</td><td>{_series_values(s)}</td></tr>'
        for s in facts.vitals
    )
    return f'<section data-section="vitals"><h2>{_t("vitals")}</h2><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></section>'


def _remarks(facts: Facts) -> str:
    if not facts.remarks:
        return ""
    items = []
    for r in facts.remarks:
        scope, target = remark_target(r)
        where = f'<span class="muted">{_t("on_" + scope)}</span>'
        if target is not None:
            where += f" {_v(target)}"
        items.append(
            f'<li data-item="{esc(r.item.id)}" data-scope="{scope}"><strong>{_t(r.item.printed.kind.value)}</strong> '
            f"({where}): {_v(r.item.printed.text)} ({_date(r.doc)}, {_cite(r)}){_awaiting(r)}</li>"
        )
    return f'<section data-section="remarks"><h2>{_t("remarks")}</h2><ul>{"".join(items)}</ul></section>'


def _timeline(facts: Facts) -> str:
    rows = "".join(
        f'<tr data-doc="D{d.number}" data-document-id="{esc(d.document_id)}"><td>D{d.number}</td>'
        f"<td>{_date(d)}</td><td>{_t(d.type.value if d.parsed else 'other')}</td>"
        f"<td>{_v(d.name)}</td><td>{_t(d.status)}</td></tr>"
        for d in facts.documents
    )
    return f'<section data-section="timeline"><h2>{_t("timeline")}</h2><table><tbody>{rows}</tbody></table></section>'


def _overview(overview: Overview) -> str:
    if overview.text is not None:
        body = f'<p data-overview="shown">{esc(overview.text)}</p><p class="muted">{_t("overview_note")}</p>'
    else:
        body = f'<p class="muted" data-overview="{esc(overview.outcome)}">{_t("overview_" + overview.outcome)}</p>'
    return f'<section data-section="overview"><h2>{_t("overview")}</h2>{body}</section>'


def summary_html(facts: Facts, overview: Overview, *, generated_at: datetime) -> str:
    body = [
        _header(facts, generated_at),
        _attention(facts),
        _medicines(facts),
        _conditions(facts),
        _labs(facts),
        _vitals(facts),
        _remarks(facts),
        _timeline(facts),
        _overview(overview),
        f"<footer>{_t('footer')}</footer>",
    ]
    return page(TEXT["title"], "\n".join(b for b in body if b))
