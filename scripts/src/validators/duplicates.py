"""
Validator 10: no item read twice. Two items of the same kind that say
exactly the same thing (every printed part but the citation equal) on
overlapping cited lines are one printed item parsed twice. Every other
check passes such a copy, since its values are on its lines, and the
summary would show the value twice or count it twice. It is an error on
the later copy, so a re-parse can drop it; one that survives repair and
the fallback is LC-19.

Items of a kind may share a line when they differ ("BP 130/80, Pulse
82" is two vitals on one line), so only an exact copy counts.
See docs/adr/0004-deterministic-checks-decide-routing.md.
"""

from record.document import DocumentRecord
from validators.findings import Finding, Severity, Validator

_KINDS = (
    ("observations", "result"),
    ("medications", "medicine"),
    ("vitals", "vital"),
    ("conditions", "condition"),
    ("allergies", "allergy"),
    ("remarks", "remark"),
)


def _lines(item) -> set[tuple[int, int]]:
    c = item.printed.citation
    return {(c.page, n) for n in range(c.first_line, c.last_line + 1)}


def _identity(item) -> str:
    """Everything the item says, without where it says it."""
    return item.printed.model_dump_json(exclude={"citation"})


def check_duplicates(record: DocumentRecord) -> list[Finding]:
    findings = []
    for field, kind in _KINDS:
        seen: dict[str, list] = {}
        for item in getattr(record, field):
            earlier = seen.setdefault(_identity(item), [])
            first = next((e for e in earlier if _lines(e) & _lines(item)), None)
            if first is not None:
                c = item.printed.citation
                findings.append(
                    Finding(
                        validator=Validator.DUPLICATES,
                        severity=Severity.ERROR,
                        message=f"this {kind} is the same as {first.id}, read from "
                        "the same lines: the record has it twice",
                        item_id=item.id,
                        field="citation",
                        page=c.page,
                        lines=list(range(c.first_line, c.last_line + 1)),
                        details={"same_as": first.id},
                    )
                )
            earlier.append(item)
    return findings
