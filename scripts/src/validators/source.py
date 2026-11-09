"""
Validators 1 and 2: every extracted value is where its citation says, in
the transcription and, for PDFs with a text layer, in the PDF's own text.

A citation's quote must be an exact part of its cited lines, compared as
whole tokens with spacing and Unicode forms ignored (text.contains). The
parse prompt asks for whole lines, but small models often quote only the
cell or value they need; that is still a faithful quote, and a repair would
gain nothing. Text that is not on those lines remains an error.

Validator 1 only catches values lost or changed between transcription and
parse; a misread in the image is in both. Validator 2 is the one check on
the vision step, and only where a text layer exists (decisions 24, 40).
"""

from record.document import DocumentRecord
from validators.fields import CitedField, Place, cited_fields, item_citations
from validators.findings import Finding, Severity, Validator
from validators.text import cited_lines, contains, lines_before, squash


def check_source_text(record: DocumentRecord) -> list[Finding]:
    findings = []
    unresolved = set()
    for item_id, field, citation in item_citations(record):
        lines = cited_lines(record, citation)
        where = {"item_id": item_id, "field": field, "page": citation.page,
                 "lines": list(range(citation.first_line, citation.last_line + 1))}  # fmt: skip
        if lines is None:
            unresolved.add((item_id, field))
            findings.append(_error(
                f"cites page {citation.page}, lines {citation.first_line}-"
                f"{citation.last_line}, which are not in the transcription", **where))  # fmt: skip
        elif not contains("\n".join(lines), citation.quote):
            findings.append(_error(
                "the quote is not on the cited lines",
                details={"quote": citation.quote, "lines": "\n".join(lines)}, **where))  # fmt: skip

    for f in cited_fields(record):
        if (f.item_id, f.field if f.is_header else None) in unresolved:
            continue
        if not _found(record, f):
            findings.append(_error(
                f"{f.field} {f.text!r} is not {f.place.value}",
                item_id=f.item_id, field=f.field, page=f.citation.page,
                lines=[f.citation.first_line]))  # fmt: skip
    return findings


def _found(record: DocumentRecord, f: CitedField) -> bool:
    lines = cited_lines(record, f.citation)
    if contains("\n".join(lines), f.text):
        return True
    if f.place is Place.ON_OR_ABOVE:
        above = lines_before(record, f.citation.page, f.citation.first_line)
        return any(contains(line, f.text) for line in above)
    return False


def _error(message: str, **kw) -> Finding:
    return Finding(
        validator=Validator.SOURCE_TEXT, severity=Severity.ERROR, message=message, **kw
    )


def check_text_layer(record: DocumentRecord) -> list[Finding]:
    """
    Compared with spacing removed and NFKC applied, since PyMuPDF splits
    table cells over lines and keeps ligatures. A field's page must have a
    text layer; a value "above" may be on an earlier page.
    """
    if not record.document.has_text_layer:
        return []
    layers = [squash(p.text_layer or "") for p in record.pages]
    findings = []
    for f in cited_fields(record):
        index = f.citation.page - 1
        if index >= len(layers) or not layers[index]:
            continue
        text = (
            layers[index] if f.place is Place.ON_LINES else "".join(layers[: index + 1])
        )
        if squash(f.text) not in text:
            findings.append(Finding(
                validator=Validator.TEXT_LAYER, severity=Severity.FLAG,
                message=f"{f.field} {f.text!r} is not in the PDF's own text of page "
                        f"{f.citation.page}: the transcription may have misread it",
                item_id=f.item_id, field=f.field, page=f.citation.page,
                lines=[f.citation.first_line],
                details={"transcription": f.citation.quote}))  # fmt: skip
    return findings
