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
the vision step, and only where a text layer exists.
See docs/adr/0012-pdf-text-layer-check.md.
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


# The field that finds an item's row in the text layer: a result's test
# name, a medicine's or vital's name. Items without one (a condition, a
# remark, an allergy, a header value) are a single text, checked on the page.
_ROW_ANCHORS = ("test_name", "name")

# Characters a row's window may differ by between the transcription and the
# PDF's text (a Markdown mark, a cell the transcription spells differently).
_ROW_SLACK = 2


def check_text_layer(record: DocumentRecord) -> list[Finding]:
    """
    Compared with spacing removed and NFKC applied, since PyMuPDF splits
    table cells over lines and keeps ligatures. A field's page must have a
    text layer; a value "above" may be on an earlier page.

    Per row: a field of an item with a test or medicine name must be in the
    text layer next to that name, within the length of its transcribed row
    either side of where the name sits in it, not merely somewhere on the
    page. Otherwise a misread value that happens to be printed in another
    row passes (creatinine "1.7" read for "1.6", found inside the next
    row's "mL/min/1.73m²"). A value is looked for in its row with the
    item's own reference ranges taken out, so a misread that lands on a
    range bound printed in that row (eosinophils "6" read for "5" beside
    "1 - 6") is flagged too. A name the text layer doesn't have is
    flagged itself, and the item's other fields are then checked on the
    page, since the row can't be found.
    """
    if not record.document.has_text_layer:
        return []
    layers = [squash(p.text_layer or "") for p in record.pages]
    fields = list(cited_fields(record))
    anchors, ranges = {}, {}
    for f in fields:
        if f.field in _ROW_ANCHORS and f.place is Place.ON_LINES:
            anchors.setdefault(f.item_id, f)
        if f.field.startswith("reference_ranges") and squash(f.text):
            ranges.setdefault(f.item_id, []).append(squash(f.text))
    findings = []
    for f in fields:
        index = f.citation.page - 1
        if index >= len(layers) or not layers[index]:
            continue
        needle = squash(f.text)
        if f.place is not Place.ON_LINES:
            found, where = needle in "".join(layers[: index + 1]), "page"
        elif (windows := _row_windows(record, f, anchors.get(f.item_id),
                                      layers[index])) is not None:  # fmt: skip
            if f.field == "value_text":
                # A range printed as the value itself ("Nil", reference
                # "Nil") stays: taking it out would take the value too.
                own = [r for r in ranges.get(f.item_id, []) if r != needle]
                windows = [_without(w, own) for w in windows]
            found, where = any(needle in w for w in windows), "row"
        else:
            found, where = needle in layers[index], "page"
        if not found:
            findings.append(Finding(
                validator=Validator.TEXT_LAYER, severity=Severity.FLAG,
                message=f"{f.field} {f.text!r} is not in the PDF's own text of "
                        f"{'its row on ' if where == 'row' else ''}page "
                        f"{f.citation.page}: the transcription may have misread it",
                item_id=f.item_id, field=f.field, page=f.citation.page,
                lines=[f.citation.first_line],
                details={"transcription": f.citation.quote}))  # fmt: skip
    return findings


def _without(text: str, parts: list[str]) -> str:
    """`text` with every occurrence of each of `parts` taken out."""
    for part in parts:
        text = text.replace(part, "\x00")
    return text


def _row_windows(
    record: DocumentRecord, f: CitedField, anchor: CitedField | None, layer: str
) -> list[str] | None:
    """
    The stretches of a page's squashed text layer that can be `f`'s row:
    one per place the item's name is printed. None when the field has no
    row to be checked in (it is the name, the item has none, or the text
    layer lacks it).
    """
    if anchor is None or anchor is f or anchor.citation != f.citation:
        return None
    name = squash(anchor.text)
    lines = cited_lines(record, f.citation)
    if not name or name not in layer or lines is None:
        return None
    row = squash("".join(lines).replace("|", ""))
    at = row.find(name)
    before, after = (at, len(row) - at - len(name)) if at >= 0 else (len(row), len(row))
    windows, start = [], 0
    while (i := layer.find(name, start)) != -1:
        lo = max(0, i - before - _ROW_SLACK)
        windows.append(layer[lo : i + len(name) + after + _ROW_SLACK])
        start = i + 1
    return windows
