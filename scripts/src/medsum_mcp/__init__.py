"""
A local stdio MCP server over the read-only tools of tools/, for an MCP
client (a desktop assistant, an IDE, the eval in evals/mcp_eval.py).

It reads through the Go API, never Postgres, logged in as a dedicated
clinician account, so the organisation's scope, the account's permissions
and the audit log apply to every call: each tool call is one API request
and one audit row naming the tool. Every answer carries its citation.
Synthetic data only: it starts only under MEDSUM_ENV=dev.
See docs/adr/0012-shared-tool-layer.md.

    python -m medsum_mcp       (from scripts/src; reads scripts/mcp.env.local)
"""
