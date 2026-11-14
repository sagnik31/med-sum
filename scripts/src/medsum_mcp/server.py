"""
The stdio MCP server: lists the tools of medsum_mcp.tools and runs a
client's calls through the Go API. Log lines go to stderr (stdout carries
the protocol) and hold the tool and its outcome only.

    python -m medsum_mcp       (from scripts/src)

Settings come from the environment, then scripts/mcp.env.local (untracked;
copy scripts/mcp.env.example): MEDSUM_MCP_EMAIL and MEDSUM_MCP_PASSWORD of
the server's own clinician account, MEDSUM_MCP_API_URL. It starts only under
MEDSUM_ENV=dev, where every document is synthetic: what a tool returns goes
to the client's model, which may run at a cloud provider.
See docs/adr/0012-shared-tool-layer.md.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

import anyio
import mcp_types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from medsum_mcp import tools
from medsum_mcp.api import ApiClient, ConfigError, credentials, read_env_file
from observability.logs import configure_logging

log = logging.getLogger("medsum_mcp")

ENV_FILE = Path(__file__).resolve().parents[2] / "mcp.env.local"
SERVER_NAME = "medsum"
SERVER_VERSION = "1"


def build_server(api: ApiClient) -> Server:
    async def on_list_tools(_ctx, _params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=t.name,
                    description=t.description,
                    input_schema=t.input_schema(),
                    output_schema=t.output_schema(),
                    annotations=types.ToolAnnotations(
                        read_only_hint=True, open_world_hint=False
                    ),
                )
                for t in tools.TOOLS.values()
            ]
        )

    async def on_call_tool(
        _ctx, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        started = time.monotonic()
        answer = await tools.call(api, params.name, params.arguments or {})
        log.info("tool call", extra={"obs": tools.log_fields(answer, started)})
        if answer.error is not None:
            body = answer.error.as_dict()
            return types.CallToolResult(
                content=[types.TextContent(text=json.dumps(body))], is_error=True
            )
        return types.CallToolResult(
            content=[types.TextContent(text=json.dumps(answer.structured))],
            structured_content=answer.structured,
        )

    return Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=tools.INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def settings(environ=None, env_file: Path = ENV_FILE) -> dict[str, str]:
    """The environment over the env file: a variable set already wins."""
    values = read_env_file(env_file) if env_file.is_file() else {}
    values.update(os.environ if environ is None else environ)
    return values


async def serve(api: ApiClient) -> None:
    server = build_server(api)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )
    finally:
        await api.aclose()


def main(argv=None, environ=None, env_file: Path = ENV_FILE) -> int:
    values = settings(environ, env_file)
    if values.get("MEDSUM_ENV") != "dev":
        print(
            "medsum_mcp runs only with MEDSUM_ENV=dev: what its tools return goes "
            "to the client's model, so it may only serve synthetic data.",
            file=sys.stderr,
        )
        return 2
    try:
        creds = credentials(values)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 2
    configure_logging(console=True)
    anyio.run(serve, ApiClient(creds))
    return 0
