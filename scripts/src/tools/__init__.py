"""
Read-only tools over one document's pages, with typed inputs and outputs.

The tools know nothing of the pipeline graph: each reads a ToolSource (the
document's transcribed pages) and returns a typed answer that cites where
it came from. A repair step can offer them to a model in-process, and a
tool server can serve the same definitions later.
"""

from tools.base import (
    Tool,
    ToolError,
    ToolResult,
    ToolSource,
    invoke,
    specs,
)
from tools.transcription import GET_LINES

TOOLS: dict[str, Tool] = {t.name: t for t in (GET_LINES,)}

__all__ = [
    "GET_LINES",
    "TOOLS",
    "Tool",
    "ToolError",
    "ToolResult",
    "ToolSource",
    "invoke",
    "specs",
]
