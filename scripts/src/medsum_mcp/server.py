"""
The stdio MCP server: lists the tools of medsum_mcp.tools and runs a
client's calls through the Go API. Log lines go to stderr (stdout carries
the protocol) and hold the tool and its outcome only.

    python -m medsum_mcp       (from scripts/src)

Settings come from the environment, then scripts/mcp.env.local (untracked;
copy scripts/mcp.env.example): MEDSUM_MCP_EMAIL and MEDSUM_MCP_PASSWORD of
the server's own clinician account, MEDSUM_MCP_API_URL. It starts only under
MEDSUM_ENV=dev, where every document is synthetic: what a tool returns goes
to the client's model, which may run at a cloud provider. Its own setting is
not enough: before serving it asks the API which environment the API runs in
(GET /environment), and serves only when that is dev too.
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
from medsum_mcp.api import (
    ApiClient,
    ApiError,
    ConfigError,
    credentials,
    read_env_file,
)
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


class NotDevApi(RuntimeError):
    """The API the server would read from does not run with MEDSUM_ENV=dev."""


async def require_dev_api(api: ApiClient) -> None:
    """
    Raise NotDevApi unless the API itself runs under MEDSUM_ENV=dev: an env
    file can say dev while pointing at an API that holds real patients.
    See docs/adr/0012-shared-tool-layer.md.
    """
    try:
        env = await api.environment()
    except ApiError as e:
        raise NotDevApi(f"could not ask the API its environment ({e})") from e
    if env != "dev":
        raise NotDevApi(f"the API runs with MEDSUM_ENV={env}, not dev")


async def serve(api: ApiClient) -> None:
    try:
        await require_dev_api(api)
    except BaseException:
        await api.aclose()
        raise
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
    try:
        anyio.run(serve, ApiClient(creds))
    except NotDevApi as e:
        print(
            f"medsum_mcp serves only a development API: {e}. What its tools "
            "return goes to the client's model, so it may only serve synthetic data.",
            file=sys.stderr,
        )
        return 2
    return 0
