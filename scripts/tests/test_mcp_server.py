"""
The MCP server (medsum_mcp) against a fake Go API, through the SDK's own
in-process client: each tool's schema, citations on every answer, a
request for another organisation's data refused, one audited API request
per call, and a login again when the session ends. No database, no model.

The fake API answers as the Go API does (org scope as 404, one audit row
per read it answers, 401 before any row when the session is gone); the Go
side of that is checked in backend/internal/httpapi/tool_audit_test.go.
"""

import json
import re
from pathlib import Path

import anyio
import httpx
import pytest
from mcp import Client

from medsum_mcp import api as mcp_api
from medsum_mcp import server as mcp_server
from medsum_mcp import tools as mcp_tools
from medsum_mcp.api import ApiClient, Credentials
from observability.logs import LOG_FIELDS
from record.review import apply_review
from test_review import flagged_record, req

PATIENT = "11111111-1111-4111-8111-111111111111"
THEIR_PATIENT = "22222222-2222-4222-8222-222222222222"
DOC = "33333333-3333-4333-8333-333333333333"
THEIR_DOC = "44444444-4444-4444-8444-444444444444"
EMAIL, PASSWORD = "mcp@hospital.test", "a-test-password-only"


class FakeApi:
    """The Go API's answers to the routes the server reads, for one org."""

    def __init__(self, record):
        self.record = record
        self.tokens = 0
        self.live = set()
        self.requests = []  # (path, tool label) of every request
        self.audit = []  # one per read answered, as the API writes them

    def expire(self):
        self.live.clear()

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((path, request.headers.get(mcp_api.TOOL_HEADER)))
        if path == "/auth/login":
            body = json.loads(request.content)
            if request.headers.get("X-Requested-With") != "med-sum":
                return httpx.Response(403)
            if (body["email"], body["password"]) != (EMAIL, PASSWORD):
                return httpx.Response(401, text="wrong email or password")
            self.tokens += 1
            token = f"token-{self.tokens}"
            self.live.add(token)
            return httpx.Response(
                200,
                json={"email": EMAIL},
                headers={
                    "Set-Cookie": f"{mcp_api.SESSION_COOKIE}={token}; Path=/; HttpOnly; Secure"
                },
            )
        cookie = request.headers.get("Cookie", "")
        if cookie.removeprefix(f"{mcp_api.SESSION_COOKIE}=") not in self.live:
            return httpx.Response(401, text="please log in")  # no audit row
        answer = self._read(path, request.url.params)
        self.audit.append(
            {"path": path, "tool": request.headers.get(mcp_api.TOOL_HEADER)}
        )
        return answer

    def _read(self, path, params) -> httpx.Response:
        if path == "/patients":
            rows = [
                {"id": PATIENT, "full_name": "Asha Rao", "mrn": "SD-00417",
                 "date_of_birth": None, "age_years": 52, "sex": "F"},
            ]  # fmt: skip
            q = params.get("q", "")
            return httpx.Response(
                200, json=[r for r in rows if q.lower() in r["full_name"].lower()]
            )
        if path == f"/patients/{PATIENT}/documents":
            return httpx.Response(
                200,
                json=[{"id": DOC, "patient_id": PATIENT, "original_name": "cbc.pdf",
                       "uploaded_at": "2025-03-04T09:00:00Z", "insight_status": "needs_review"}],
            )  # fmt: skip
        if path == f"/documents/{DOC}/record":
            return httpx.Response(
                200,
                json={
                    "document_id": DOC,
                    "record": self.record.model_dump(mode="json"),
                },
            )
        # Another organisation's patient or document: not found, as the API
        # answers whether or not it exists.
        return httpx.Response(404, text="not found")


def run(api: FakeApi, calls):
    """Run `calls(client)` against a server over the fake API; its result."""
    client = ApiClient(
        Credentials("http://api.test", EMAIL, PASSWORD),
        transport=httpx.MockTransport(api.handler),
    )
    server = mcp_server.build_server(client)

    async def go():
        async with Client(server) as c:
            return await calls(c), client

    return anyio.run(go)


@pytest.fixture
def fake():
    return FakeApi(flagged_record())


def structured(result):
    assert not result.is_error, result.content[0].text
    assert json.loads(result.content[0].text) == result.structured_content
    return result.structured_content


ONE_CALL_EACH = {
    "find_patients": {"query": "asha"},
    "list_documents": {"patient_id": PATIENT},
    "get_lines": {"document_id": DOC, "page": 1, "first": 8, "last": 9},
    "search_transcription": {"document_id": DOC, "text": "haemoglobin"},
    "get_text_layer": {"document_id": DOC, "page": 1, "first": 1, "last": 5},
    "list_items": {"document_id": DOC},
    "get_item": {"document_id": DOC, "item_id": "obs-1"},
}


def test_one_call_each_covers_every_tool():
    assert set(ONE_CALL_EACH) == set(mcp_tools.TOOLS)


# -------------------------------------------------------------------
# Schemas
# -------------------------------------------------------------------
def test_every_tool_is_listed_with_its_schemas(fake):
    listed, _ = run(fake, lambda c: c.list_tools())
    tools = {t.name: t for t in listed.tools}
    assert list(tools) == list(mcp_tools.TOOLS)
    for name, tool in tools.items():
        assert tool.description, name
        schema = tool.input_schema
        assert schema["type"] == "object" and schema["additionalProperties"] is False, (
            name
        )
        out = tool.output_schema
        assert set(out["required"]) == {"citations", "result"}, name
        assert tool.annotations.read_only_hint is True, name
    for name in (
        "get_lines",
        "search_transcription",
        "get_text_layer",
        "list_items",
        "get_item",
    ):
        doc_id = tools[name].input_schema["properties"]["document_id"]
        assert name in tools and "document_id" in tools[name].input_schema["required"]
        assert re.fullmatch(doc_id["pattern"], DOC)
    assert tools["list_documents"].input_schema["required"] == ["patient_id"]
    assert tools["get_item"].input_schema["properties"]["item_id"]["maxLength"] == 40


def test_the_document_tools_keep_the_shared_tools_inputs():
    from tools import TOOLS as SHARED

    for name, shared in SHARED.items():
        ours = mcp_tools.TOOLS[name].input_schema()["properties"]
        theirs = shared.input_model.model_json_schema()["properties"]
        assert set(ours) == set(theirs) | {"document_id"}, name


def test_each_answer_fits_its_output_schema(fake):
    async def calls(c):
        return {n: await c.call_tool(n, a) for n, a in ONE_CALL_EACH.items()}

    results, _ = run(fake, calls)
    for name, result in results.items():
        mcp_tools.TOOLS[name].output_model.model_validate(structured(result))


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("get_lines", {"document_id": DOC, "page": 0, "first": 1, "last": 2}),
        ("get_lines", {"document_id": "not-an-id", "page": 1, "first": 1, "last": 2}),
        ("get_item", {"item_id": "obs-1"}),  # no document_id
        ("list_documents", {"patient_id": PATIENT, "extra": 1}),
        ("search_transcription", {"document_id": DOC, "text": "x"}),
    ],
)
def test_a_call_that_doesnt_fit_the_schema_reads_nothing(fake, name, arguments):
    result, _ = run(fake, lambda c: c.call_tool(name, arguments))
    assert result.is_error
    assert json.loads(result.content[0].text)["error"] == "malformed_arguments"
    assert fake.audit == []
    # The message names fields, never the values sent.
    assert "not-an-id" not in result.content[0].text


def test_an_unknown_tool_is_an_error_answer(fake):
    result, _ = run(fake, lambda c: c.call_tool("delete_patient", {}))
    assert result.is_error and "unknown_tool" in result.content[0].text
    assert fake.audit == []


# -------------------------------------------------------------------
# Citations: no bare values
# -------------------------------------------------------------------
CITE = re.compile(
    r"^(patient|document):[0-9a-f-]{36}(#(text-layer:)?p(\d+):(\d+)(-(\d+))?)?$"
)


def entries(value):
    """Every dict in a result that holds values of its own."""
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from entries(v)
    elif isinstance(value, list):
        for v in value:
            yield from entries(v)


def test_every_answer_carries_citations_that_point_into_the_record(fake):
    async def calls(c):
        return {n: await c.call_tool(n, a) for n, a in ONE_CALL_EACH.items()}

    results, _ = run(fake, calls)
    pages = fake.record.pages
    for name, result in results.items():
        answer = structured(result)
        assert answer["citations"], name
        for cite in answer["citations"]:
            m = CITE.match(cite)
            assert m, (name, cite)
            if m.group(4):
                page, first = int(m.group(4)), int(m.group(5))
                last = int(m.group(7) or first)
                text = (
                    pages[page - 1].text_layer.splitlines()
                    if m.group(3)
                    else pages[page - 1].lines
                )
                assert 1 <= first <= last <= len(text), (name, cite)
        # Every listed entry (a patient, a document, an item, a hit) cites
        # where it comes from.
        for key in ("patients", "documents", "items", "hits"):
            for entry in answer["result"].get(key, []):
                assert entry["cite"] in answer["citations"], (name, entry)


def test_a_hit_cites_the_line_it_quotes(fake):
    result, _ = run(
        fake,
        lambda c: c.call_tool(
            "search_transcription", ONE_CALL_EACH["search_transcription"]
        ),
    )
    hit = structured(result)["result"]["hits"][0]
    assert hit["cite"] == f"document:{DOC}#p1:8"
    assert hit["text"] == fake.record.pages[0].lines[7]


def test_get_item_reads_the_record_as_reviewed(fake):
    fake.record = apply_review(
        flagged_record(), req(field="value_text", value="13.9", note="re-read")
    ).record
    result, _ = run(
        fake,
        lambda c: c.call_tool("get_item", {"document_id": DOC, "item_id": "obs-1"}),
    )
    item = structured(result)["result"]
    assert item["printed"]["value_text"] == "13.9"
    assert item["cite"] == f"document:{DOC}#p1:8"


def test_a_corrected_item_says_who_corrected_it_and_what_was_read(fake):
    # The corrected 13.9 shows next to a cited line that still prints 10.4:
    # the answer says so, rather than leaving the two to contradict.
    fake.record = apply_review(
        flagged_record(), req(field="value_text", value="13.9", note="re-read")
    ).record
    result, _ = run(
        fake,
        lambda c: c.call_tool("get_item", {"document_id": DOC, "item_id": "obs-1"}),
    )
    review = structured(result)["result"]["review"]
    assert review["status"] == "clinician_corrected"
    last = review["last_correction"]
    assert (last["field"], last["original"], last["corrected"]) == (
        "value_text",
        "10.4",
        "13.9",
    )
    assert last["clinician"] and last["at"]


def test_a_flagged_item_says_why_and_the_list_shows_each_status(fake):
    async def calls(c):
        listed = await c.call_tool("list_items", {"document_id": DOC})
        item = await c.call_tool("get_item", {"document_id": DOC, "item_id": "obs-1"})
        return listed, item

    (listed, item), _ = run(fake, calls)
    statuses = {
        i["item_id"]: i["status"] for i in structured(listed)["result"]["items"]
    }
    assert statuses["obs-1"] == "low_confidence"
    review = structured(item)["result"]["review"]
    assert review["status"] == "low_confidence" and review["reasons"]
    assert review["last_correction"] is None


@pytest.mark.parametrize(
    "broken",
    [
        lambda r: httpx.Response(200, text="<html>not json</html>"),
        lambda r: httpx.Response(200, json={"document_id": DOC, "record": {"x": 1}}),
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")),
    ],
    ids=["not json", "not a record", "unreachable"],
)
def test_an_unreadable_api_answer_is_an_unavailable_answer(fake, broken, caplog):
    real = fake._read

    def read(path, params):
        return broken(path) if path.endswith("/record") else real(path, params)

    fake._read = read
    with caplog.at_level("WARNING", logger="medsum_mcp"):
        result, _ = run(fake, lambda c: c.call_tool("list_items", {"document_id": DOC}))
    assert result.is_error and "unavailable" in result.content[0].text
    assert any(
        getattr(r, "obs", {}).get("event") == "mcp_tool_unavailable"
        for r in caplog.records
    )


def test_an_empty_search_has_no_values_and_no_citations(fake):
    result, _ = run(
        fake,
        lambda c: c.call_tool(
            "search_transcription", {"document_id": DOC, "text": "ferritin"}
        ),
    )
    answer = structured(result)
    assert answer == {
        "citations": [],
        "result": {"hits": [], "more": False, "document_id": DOC},
    }


# -------------------------------------------------------------------
# Another organisation's data
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    "name, arguments",
    [
        ("get_item", {"document_id": THEIR_DOC, "item_id": "obs-1"}),
        ("get_lines", {"document_id": THEIR_DOC, "page": 1, "first": 1, "last": 3}),
        ("list_documents", {"patient_id": THEIR_PATIENT}),
    ],
)
def test_another_organisations_data_is_refused(fake, name, arguments):
    result, _ = run(fake, lambda c: c.call_tool(name, arguments))
    assert result.is_error
    body = json.loads(result.content[0].text)
    assert body == {
        "error": "not_found",
        "message": "not found, or not visible to this account",
    }
    assert result.structured_content is None
    # Refused by the API, which audits the attempt like any read.
    assert [a["tool"] for a in fake.audit] == [name]


# -------------------------------------------------------------------
# One audited request per call; a new login when the session ends
# -------------------------------------------------------------------
def test_each_tool_call_is_one_api_request_and_one_audit_row_naming_it(fake):
    async def calls(c):
        for name, arguments in ONE_CALL_EACH.items():
            await c.call_tool(name, arguments)

    _, client = run(fake, calls)
    assert [a["tool"] for a in fake.audit] == list(ONE_CALL_EACH)
    reads = [r for r in fake.requests if r[0] != "/auth/login"]
    assert [tool for _, tool in reads] == list(ONE_CALL_EACH)
    assert client.logins == 1


def test_the_server_logs_in_again_when_the_session_ends(fake):
    async def calls(c):
        await c.call_tool("list_items", {"document_id": DOC})
        fake.expire()
        return await c.call_tool("list_items", {"document_id": DOC})

    result, client = run(fake, calls)
    assert not result.is_error
    assert client.logins == 2
    assert len(fake.audit) == 2  # the refused request left no row


def test_a_login_that_fails_is_an_unavailable_answer_with_no_password(fake):
    async def calls(c):
        return await c.call_tool("list_items", {"document_id": DOC})

    client = ApiClient(
        Credentials("http://api.test", EMAIL, "wrong"),
        transport=httpx.MockTransport(fake.handler),
    )

    async def go():
        async with Client(mcp_server.build_server(client)) as c:
            return await calls(c)

    result = anyio.run(go)
    assert result.is_error and "unavailable" in result.content[0].text
    assert "wrong" not in result.content[0].text
    assert fake.audit == []


# -------------------------------------------------------------------
# Logs and settings
# -------------------------------------------------------------------
def test_a_calls_log_fields_are_its_tool_and_outcome_only():
    answer = mcp_tools.Answer(
        "get_item", error=mcp_tools.ErrorAnswer("not_found", "x"), api_status=404
    )
    fields = mcp_tools.log_fields(answer, 0.0)
    assert set(fields) == {"event", "tool", "ok", "duration_ms", "error_kind", "status"}
    assert set(fields) <= LOG_FIELDS


def test_credentials_never_show_the_password():
    creds = mcp_api.credentials(
        {"MEDSUM_MCP_EMAIL": EMAIL, "MEDSUM_MCP_PASSWORD": PASSWORD}
    )
    assert PASSWORD not in repr(creds) and creds.api_url == mcp_api.DEFAULT_API_URL


@pytest.mark.parametrize("env", ["", "prod", "staging"])
def test_the_server_starts_only_in_development(env, capsys, tmp_path):
    environ = {
        "MEDSUM_ENV": env,
        "MEDSUM_MCP_EMAIL": EMAIL,
        "MEDSUM_MCP_PASSWORD": PASSWORD,
    }
    assert mcp_server.main(environ=environ, env_file=tmp_path / "none") == 2
    assert "MEDSUM_ENV=dev" in capsys.readouterr().err


def test_the_server_needs_its_account(capsys, tmp_path):
    assert (
        mcp_server.main(environ={"MEDSUM_ENV": "dev"}, env_file=tmp_path / "none") == 2
    )
    assert "MEDSUM_MCP_EMAIL" in capsys.readouterr().err


def test_settings_come_from_the_env_file_under_the_environment(tmp_path):
    env_file = tmp_path / "mcp.env.local"
    env_file.write_text(
        "# the server's account\nMEDSUM_MCP_EMAIL=a@b.test\nMEDSUM_ENV=dev\n",
        encoding="utf-8",
    )
    values = mcp_server.settings({"MEDSUM_ENV": "prod"}, env_file)
    assert values["MEDSUM_MCP_EMAIL"] == "a@b.test" and values["MEDSUM_ENV"] == "prod"


def test_the_env_file_is_untracked_and_its_example_holds_no_secret():
    root = Path(__file__).resolve().parents[2]
    assert "scripts/mcp.env.local" in (root / ".gitignore").read_text(encoding="utf-8")
    example = (root / "scripts" / "mcp.env.example").read_text(encoding="utf-8")
    assert re.search(r"^MEDSUM_MCP_PASSWORD=\s*$", example, re.M)
