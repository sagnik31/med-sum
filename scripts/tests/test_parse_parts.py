"""
The parse in parts (pipeline/parts.py): how a document's pages are cut
into parts, what each part's call reads, and how the parts' parses are
merged back into one.
"""

import pytest

from dataset_records import DOCS, PARSED_IDS, key, parse_of, split_parse
from pipeline import parts
from record.common import Citation, PrintedText
from record.document import PageTranscription
from record.printed import DocumentType

LAB, RX = DocumentType.LAB_REPORT, DocumentType.PRESCRIPTION


def covered(plan, pages) -> dict[int, list[int]]:
    """Each page's lines, in the order the parts read them (context left out)."""
    seen: dict[int, list[int]] = {p.page: [] for p in pages}
    for part in plan:
        seen[part.page] += list(range(part.first_line, part.last_line + 1))
    return seen


@pytest.mark.parametrize("doc_id", PARSED_IDS)
@pytest.mark.parametrize("max_rows", [None, 3])
def test_parts_read_every_line_of_every_page_once(doc_id, max_rows):
    record = key(doc_id)
    plan = parts.plan(record.pages, record.document.type, max_rows=max_rows)

    assert [p.index for p in plan] == list(range(len(plan)))
    for page in record.pages:
        assert covered(plan, record.pages)[page.page] == list(
            range(1, len(page.lines) + 1)
        )


LONG = "d25"  # 27 results on each of its first two pages


@pytest.mark.parametrize("doc_id", [d for d in PARSED_IDS if d != LONG])
def test_the_other_documents_are_one_part_per_page(doc_id):
    record = key(doc_id)
    plan = parts.plan(record.pages, record.document.type)
    assert [p.page for p in plan] == [p.page for p in record.pages if p.lines]
    assert all(p.context == [] for p in plan)


def test_a_page_of_more_rows_than_a_part_holds_is_cut_mid_table():
    record = key(LONG)
    plan = parts.plan(record.pages, record.document.type)

    assert [p.page for p in plan] == [1, 1, 2, 2, 3, 4]
    for first, second in (plan[0:2], plan[2:4]):
        assert second.first_line == first.last_line + 1
        # The second part starts inside a table: it gets that table's
        # heading and header rows, which the first part holds.
        assert second.context and all(
            first.holds(first.page, n) for n in second.context
        )
    rows = {p.index: sum(1 for o in record.observations
                         if p.holds(o.printed.citation.page, o.printed.citation.first_line))
            for p in plan}  # fmt: skip
    assert sum(rows.values()) == 60
    assert max(rows.values()) <= parts.MAX_ROWS_PER_PART


@pytest.mark.parametrize("doc_id", PARSED_IDS)
@pytest.mark.parametrize("max_rows", [None, 2])
def test_merging_the_parts_gives_the_whole_parse(doc_id, max_rows):
    record = key(doc_id)
    plan = parts.plan(record.pages, record.document.type, max_rows=max_rows)
    pieces = split_parse(parse_of(record), plan)

    assert parts.merge(pieces) == parse_of(record)


def test_a_page_is_cut_into_even_shares_between_rows():
    lines = ["# Lab", "| Test | Result |", "| --- | --- |"] + [
        f"| t{n} | {n} |" for n in range(1, 12)
    ]  # 11 rows, lines 4-14
    page = PageTranscription(page=1, lines=lines)

    plan = parts.plan([page], LAB, max_rows=5)

    # At most 5 rows each, as even as can be: 4, 4 and 3.
    assert [(p.first_line, p.last_line) for p in plan] == [(1, 7), (8, 11), (12, 14)]
    assert plan[1].context == [1, 2, 3]


def test_a_part_inside_a_table_reads_the_heading_and_header_first():
    record = key("d01")
    plan = parts.plan(record.pages, LAB, max_rows=5)
    second = [p for p in plan if p.page == 1][1]
    lines = record.pages[0].lines

    assert [lines[n - 1] for n in second.context] == [
        "## COMPLETE BLOOD COUNT",
        "| Test | Result | Flag | Unit | Reference Range |",
        "| --- | --- | --- | --- | --- |",
    ]
    text = parts.part_text(record.pages, second)
    assert text.splitlines()[0] == "=== Page 1 ==="
    assert text.splitlines()[1] == f"{second.context[0]}| ## COMPLETE BLOOD COUNT"
    assert text.splitlines()[4].startswith(f"{second.first_line}| ")


def test_a_part_at_a_table_s_first_row_starts_at_its_heading():
    lines = ["# Lab", "", "## A", "", "| Test | Result |", "| --- | --- |",
             "| a1 | 1 |", "| a2 | 2 |", "", "## B", "", "| Test | Result |",
             "| --- | --- |", "| b1 | 1 |", "| b2 | 2 |"]  # fmt: skip
    page = PageTranscription(page=1, lines=lines)

    plan = parts.plan([page], LAB, max_rows=2)

    assert [(p.first_line, p.last_line, p.context) for p in plan] == [
        (1, 9, []), (10, 15, []),
    ]  # fmt: skip


def test_prescriptions_are_cut_by_medicine_lines():
    lines = ["# Clinic", "Rx"] + [f"Tab. Med{n} 10 mg 1-0-1" for n in range(1, 7)]
    page = PageTranscription(page=1, lines=lines)

    plan = parts.plan([page], RX, max_rows=3)

    assert [(p.first_line, p.last_line) for p in plan] == [(1, 5), (6, 8)]


def test_a_page_without_lines_is_not_parsed_unless_it_is_all_there_is():
    pages = [
        PageTranscription(page=1, lines=["a"]),
        PageTranscription(page=2, lines=[]),
    ]
    assert [p.page for p in parts.plan(pages, LAB)] == [1]
    empty = [PageTranscription(page=1, lines=[])]
    assert [(p.page, p.first_line, p.last_line) for p in parts.plan(empty, LAB)] == [
        (1, 1, 0)
    ]


def text(value: str, page: int = 1, line: int = 1) -> PrintedText:
    return PrintedText(
        citation=Citation(page=page, first_line=line, last_line=line, quote=value),
        text=value,
    )


def test_details_come_from_the_first_part_that_has_them():
    first, second = split_parse(
        parse_of(key("d01")), parts.plan(key("d01").pages, LAB)
    )[:2]
    second.patient.name = text("Somebody Else", page=2)
    second.details.facility = text("Another Lab", page=2)
    first.details.collection_date = None
    second.details.collection_date = text("01/01/2024", page=2)

    merged = parts.merge([first, second])

    assert merged.patient.name == first.patient.name
    assert merged.details.facility == first.details.facility
    assert merged.details.collection_date.text == "01/01/2024"


def test_clinicians_are_listed_once_each_in_part_order():
    a, b = split_parse(parse_of(key("d01")), parts.plan(key("d01").pages, LAB))[:2]
    a.details.clinicians = [text("Dr. A. Rao")]
    b.details.clinicians = [text("DR.  a. rao", page=2), text("Dr. B. Sen", page=2)]

    merged = parts.merge([a, b])

    assert [c.text for c in merged.details.clinicians] == ["Dr. A. Rao", "Dr. B. Sen"]


def test_nothing_to_merge_is_a_bug():
    with pytest.raises(ValueError):
        parts.merge([])


def test_the_parts_of_the_dataset_s_longest_document():
    longest = max((d for d in PARSED_IDS if d != LONG), key=lambda d: DOCS[d]["pages"])
    record = key(longest)
    assert len(parts.plan(record.pages, record.document.type)) == len(record.pages)


# -------------------------------------------------------------------
# Details the parts print differently (LC-20)
# -------------------------------------------------------------------
@pytest.mark.parametrize("doc_id", PARSED_IDS)
@pytest.mark.parametrize("max_rows", [None, 2])
def test_the_dataset_s_parts_agree_on_their_details(doc_id, max_rows):
    record = key(doc_id)
    plan = parts.plan(record.pages, record.document.type, max_rows=max_rows)
    assert parts.header_conflicts(split_parse(parse_of(record), plan)) == []


def test_a_later_part_printed_for_another_patient_or_date_is_a_conflict():
    first, second = split_parse(
        parse_of(key("d01")), parts.plan(key("d01").pages, LAB)
    )[:2]
    first.patient.name = text("Mrs. Meera Iyer")
    second.patient.name = text("Somebody Else", page=2)
    first.details.report_date = text("05/09/2024")
    second.details.report_date = text("06/09/2024", page=2)
    first.details.facility = text("Sunrise Lab")
    second.details.facility = text("Another Lab", page=2)  # letterheads vary

    conflicts = parts.header_conflicts([first, second])

    assert conflicts == [
        {"field": "patient.name", "parts": [0, 1]},
        {"field": "details.report_date", "parts": [0, 1]},
    ]
    # Field names and part numbers only: no patient data.
    assert "Somebody" not in str(conflicts) and "2024" not in str(conflicts)


def test_the_same_details_printed_another_way_are_no_conflict():
    first, second = split_parse(
        parse_of(key("d01")), parts.plan(key("d01").pages, LAB)
    )[:2]
    pairs = {
        "name": ("Mrs. Meera Iyer", "MEERA  IYER"),
        "age": ("57 Y", "57 Years"),
        "sex": ("F", "Female"),
        "date_of_birth": ("05/09/1967", "5 Sep 1967"),
        "patient_id": ("UHID 4471", "uhid4471"),
    }
    for field, (a, b) in pairs.items():
        setattr(first.patient, field, text(a))
        setattr(second.patient, field, text(b, page=2))
    second.details.report_date = None  # printed on one part only

    assert parts.header_conflicts([first, second]) == []


def named(*names):
    """One parse per name (None: the part prints none)."""
    plan = parts.plan(key("d01").pages, LAB)
    first = split_parse(parse_of(key("d01")), plan)[0]
    out = []
    for name in names:
        p = first.model_copy(deep=True)
        p.patient.name = text(name) if name else None
        out.append(p)
    return out


def test_two_names_that_both_match_an_abbreviated_one_are_a_conflict():
    conflicts = parts.header_conflicts(named("Avery R.", "Avery Reed", "Avery Ross"))
    assert conflicts == [{"field": "patient.name", "parts": [0, 1, 2]}]


def test_a_conflict_stays_when_a_later_round_leaves_the_detail_out():
    old = parts.header_conflicts(named("Avery Reed", "Avery Reed", "Avery Ross"))
    assert old
    assert parts.kept_conflicts(old, named("Avery Reed", "Avery Reed", None)) == old


def test_a_conflict_goes_when_every_part_is_read_again_and_agrees():
    old = parts.header_conflicts(named("Avery Reed", "Avery Ross"))
    assert parts.kept_conflicts(old, named("Avery Reed", "Avery Reed")) == []
