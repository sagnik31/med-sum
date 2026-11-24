"""The API's list of MCP tools is the MCP server's own."""

import re
from pathlib import Path

from medsum_mcp.tools import TOOLS

AUDIT_GO = Path(__file__).parents[2] / "backend/internal/httpapi/audit.go"


def test_the_api_audits_every_tool_by_name():
    """A tool missing from audit.go would be audited as "unknown" (ADR 0012)."""
    text = AUDIT_GO.read_text(encoding="utf-8")
    block = re.search(r"var mcpTools = map\[string\]bool\{(.*?)\n\}", text, re.S)
    assert set(re.findall(r'"(\w+)":', block[1])) == set(TOOLS)
