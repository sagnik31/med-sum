"""
Tools that read the record built from the parse: its items, what each
says, and the lines its citation points at.
"""

from typing import Any

from pydantic import Field

from record.document import DocumentRecord
from tools.base import Tool, ToolError, ToolInput, ToolOutput, ToolSource, cite

# The record's item groups, with the kind each item is listed as.
GROUPS = {
    "observations": "lab_result",
    "medications": "medicine",
    "conditions": "condition",
    "remarks": "remark",
    "vitals": "vital",
    "allergies": "allergy",
    "procedures": "procedure",
    "encounters": "encounter",
    "imaging_findings": "imaging_finding",
}
# The field that names an item, per kind, for the list.
NAME_FIELDS = ("test_name", "name", "text", "finding", "kind")


def _items(record: DocumentRecord):
    for group, kind in GROUPS.items():
        for item in getattr(record, group):
            yield kind, item


def _name(printed) -> str | None:
    for name in NAME_FIELDS:
        value = getattr(printed, name, None)
        if isinstance(value, str) and value:
            return value
    return None


def _no_record() -> ToolError:
    return ToolError("not_found", "there is no record: the document has no parse yet")


# -------------------------------------------------------------------
# list_items
# -------------------------------------------------------------------
class ListItemsInput(ToolInput):
    kind: str | None = Field(
        default=None,
        description="Only items of this kind: " + ", ".join(GROUPS.values()) + ".",
    )


class ItemSummary(ToolOutput):
    item_id: str
    kind: str
    name: str | None
    cite: str


class ListItemsOutput(ToolOutput):
    items: list[ItemSummary]

    def sizes(self) -> dict[str, int]:
        return {"items": len(self.items)}


def _list_items(
    source: ToolSource, args: ListItemsInput
) -> ListItemsOutput | ToolError:
    if source.record is None:
        return _no_record()
    if args.kind is not None and args.kind not in GROUPS.values():
        return ToolError(
            "malformed_arguments", "kind: not one of " + ", ".join(GROUPS.values())
        )
    items = []
    for kind, item in _items(source.record):
        if args.kind not in (None, kind):
            continue
        c = item.printed.citation
        items.append(
            ItemSummary(
                item_id=item.id,
                kind=kind,
                name=_name(item.printed),
                cite=cite(c.page, c.first_line, c.last_line),
            )
        )
    return ListItemsOutput(items=items)


LIST_ITEMS = Tool(
    name="list_items",
    description=(
        "List the items of the document's record (lab results, medicines, "
        "conditions, remarks, ...): each item's id, kind, name and where it "
        "is cited."
    ),
    input_model=ListItemsInput,
    output_model=ListItemsOutput,
    run=_list_items,
)


# -------------------------------------------------------------------
# get_item
# -------------------------------------------------------------------
class GetItemInput(ToolInput):
    item_id: str = Field(
        min_length=1, max_length=40, description="An item id, e.g. obs-3 or med-1."
    )


class GetItemOutput(ToolOutput):
    item_id: str
    kind: str
    printed: dict[str, Any] = Field(description="The item as parsed, as printed.")
    derived: dict[str, Any] | None = Field(
        description="What code worked out from it (numbers, flags), if anything."
    )
    cited_lines: list[str] = Field(
        description="The transcription's text of the lines the citation points at."
    )
    cite: str

    def sizes(self) -> dict[str, int]:
        return {"lines_returned": len(self.cited_lines)}


def _get_item(source: ToolSource, args: GetItemInput) -> GetItemOutput | ToolError:
    if source.record is None:
        return _no_record()
    found = next(
        ((k, i) for k, i in _items(source.record) if i.id == args.item_id), None
    )
    if found is None:
        return ToolError("not_found", "no item with that id; list_items lists them")
    kind, item = found
    c = item.printed.citation
    page = source.page(c.page)
    lines = (
        page.lines[c.first_line - 1 : c.last_line]
        if page is not None and c.last_line <= len(page.lines)
        else []
    )
    derived = getattr(item, "derived", None)
    return GetItemOutput(
        item_id=item.id,
        kind=kind,
        printed=item.printed.model_dump(mode="json"),
        derived=derived.model_dump(mode="json", exclude_none=True) if derived else None,
        cited_lines=lines,
        cite=cite(c.page, c.first_line, c.last_line),
    )


GET_ITEM = Tool(
    name="get_item",
    description=(
        "Return one item of the record by its id: the item as parsed, what "
        "code derived from it, and the transcription's text of the lines its "
        "citation points at, so the two can be compared."
    ),
    input_model=GetItemInput,
    output_model=GetItemOutput,
    run=_get_item,
)
