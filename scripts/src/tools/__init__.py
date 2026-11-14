"""
Read-only tools over one document, with typed inputs and outputs.

The tools know nothing of the pipeline graph: each reads a ToolSource (the
document's transcribed pages, their text layers and the record built from
its parse) and returns a typed answer that cites where it came from. The
repair step offers them to a model in-process, and the MCP server
(medsum_mcp) serves the same definitions through the Go API.
See docs/adr/0012-shared-tool-layer.md.

    get_lines             numbered lines of one page of the transcription
    search_transcription  the lines that contain some text
    get_text_layer        the PDF's own text for a page
    list_items            the record's items, with their citations
    get_item              one item, with the text of the lines it cites
"""

from tools.base import (
    Tool,
    ToolError,
    ToolResult,
    ToolSource,
    cite,
    invoke,
    specs,
)
from tools.record import GET_ITEM, LIST_ITEMS
from tools.transcription import GET_LINES, GET_TEXT_LAYER, SEARCH_TRANSCRIPTION

TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (GET_LINES, SEARCH_TRANSCRIPTION, GET_TEXT_LAYER, LIST_ITEMS, GET_ITEM)
}


def subset(*names: str) -> dict[str, Tool]:
    """Some of the tools, in TOOLS' order."""
    unknown = set(names) - TOOLS.keys()
    if unknown:
        raise KeyError(f"unknown tools: {', '.join(sorted(unknown))}")
    return {n: t for n, t in TOOLS.items() if n in names}


__all__ = [
    "GET_ITEM",
    "GET_LINES",
    "GET_TEXT_LAYER",
    "LIST_ITEMS",
    "SEARCH_TRANSCRIPTION",
    "TOOLS",
    "Tool",
    "ToolError",
    "ToolResult",
    "ToolSource",
    "cite",
    "invoke",
    "specs",
    "subset",
]
