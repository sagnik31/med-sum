"""
The MCP server's client of the Go API: a password login as the server's
clinician account, and GETs that carry the tool they are made for.

The session token is kept from the login's Set-Cookie and sent as a Cookie
header, so a Secure cookie works over http://localhost too. When the
session ends (idle or absolute timeout, a revoke, a password change), the
API answers 401: the client logs in again once and repeats the request.
A 401 is answered before any audit row is written, so a repeated request
is still one audit row.

Errors name the status and the route's shape, never a value: the API's
404 says nothing about whether another organisation's document exists.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

SESSION_COOKIE = "medsum_session"
# Every request that changes something needs it (the login does).
CSRF_HEADER = {"X-Requested-With": "med-sum"}
TOOL_HEADER = "X-Medsum-Tool"
USER_AGENT = "medsum-mcp"
DEFAULT_API_URL = "http://localhost:8080"
TIMEOUT_S = 30.0


class ConfigError(RuntimeError):
    pass


class ApiError(RuntimeError):
    """A request the API refused or failed. `status` is its HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Credentials:
    api_url: str
    email: str
    password: str

    def __repr__(self) -> str:  # never print the password
        return f"Credentials(api_url={self.api_url!r}, email={self.email!r})"


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; blank lines and lines starting with # are skipped."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def credentials(environ=os.environ) -> Credentials:
    email = environ.get("MEDSUM_MCP_EMAIL", "")
    password = environ.get("MEDSUM_MCP_PASSWORD", "")
    if not email or not password:
        raise ConfigError(
            "MEDSUM_MCP_EMAIL and MEDSUM_MCP_PASSWORD are required "
            "(copy scripts/mcp.env.example to scripts/mcp.env.local)"
        )
    url = environ.get("MEDSUM_MCP_API_URL", DEFAULT_API_URL).rstrip("/")
    check_api_url(url)
    return Credentials(api_url=url, email=email, password=password)


# Hosts plain http may reach: this machine, or a name reserved for local
# and test use (RFC 6761), which never resolves to another network.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
LOCAL_SUFFIXES = (".localhost", ".test")


def check_api_url(url: str) -> None:
    """
    The API URL the server logs in to with its password: https anywhere,
    plain http only to this machine. Anything else is refused at start-up,
    so a mistyped or planted URL can't receive the password in clear text.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and host:
        return
    if parts.scheme == "http" and (
        host in LOCAL_HOSTS or host.endswith(LOCAL_SUFFIXES)
    ):
        return
    raise ConfigError(
        "MEDSUM_MCP_API_URL must be https, or http to this machine "
        "(localhost, 127.0.0.1)"
    )


class ApiClient:
    def __init__(
        self, creds: Credentials, transport: httpx.AsyncBaseTransport | None = None
    ):
        self._creds = creds
        self._http = httpx.AsyncClient(
            base_url=creds.api_url,
            transport=transport,
            timeout=TIMEOUT_S,
            headers={"User-Agent": USER_AGENT},
        )
        self._session: str | None = None
        self.logins = 0  # how many times it logged in, for tests and logs

    async def aclose(self) -> None:
        await self._http.aclose()

    async def login(self) -> None:
        resp = await self._http.post(
            "/auth/login",
            json={"email": self._creds.email, "password": self._creds.password},
            headers=CSRF_HEADER,
        )
        if resp.status_code != 200:
            raise ApiError(resp.status_code, f"login failed ({resp.status_code})")
        token = resp.cookies.get(SESSION_COOKIE)
        if not token:
            raise ApiError(resp.status_code, "login answered without a session")
        self._session = token
        self.logins += 1

    async def environment(self) -> str:
        """The API's own MEDSUM_ENV, as GET /environment reports it."""
        answer = await self.get("/environment", tool="environment_check")
        env = answer.get("environment") if isinstance(answer, dict) else None
        if not isinstance(env, str):
            raise ApiError(200, "the API did not say which environment it runs in")
        return env

    async def get(self, path: str, *, tool: str, params: dict | None = None) -> Any:
        """GET path as the session, labelled with the tool; the JSON answer."""
        if self._session is None:
            await self.login()
        resp = await self._get(path, tool, params)
        if resp.status_code == 401:
            await self.login()
            resp = await self._get(path, tool, params)
        if resp.status_code == 404:
            raise ApiError(404, "not found, or not visible to this account")
        if resp.status_code == 403:
            raise ApiError(403, "this account is not allowed to read it")
        if resp.status_code != 200:
            raise ApiError(resp.status_code, f"the API answered {resp.status_code}")
        return resp.json()

    async def _get(self, path, tool, params) -> httpx.Response:
        return await self._http.get(
            path,
            params=params,
            headers={TOOL_HEADER: tool, "Cookie": f"{SESSION_COOKIE}={self._session}"},
        )
