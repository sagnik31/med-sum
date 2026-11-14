"""
The AI service's settings that must not silently default: where its
databases are. Every module reads them from here, never from os.environ.

- DATABASE_URL: the service's database (the same variable as the Go
  backend). Outside development it should connect as the least-privilege
  role in db/ai_service_role.sql, not as a superuser.
- MEDSUM_EVAL_ADMIN_URL: a server connection for the eval runner, which
  creates and drops its own throwaway database there. Only the eval runner
  needs it.
- INTERNAL_API_TOKEN: the secret the Go backend sends on /internal/*
  calls. Required everywhere, development included (each service reads it
  from its untracked .env.local), and at least MIN_TOKEN_LENGTH characters,
  so the service never starts with a guessable token.
- MEDSUM_ENV: "dev" on a developer's machine. Only then may a missing URL
  fall back to the local development database (DEV_DATABASE_URL,
  DEV_ADMIN_URL). Anything else, including no MEDSUM_ENV at all, fails
  closed: a missing URL is an error, raised when the service starts
  (insights_service's lifespan calls load()) rather than at the first query.

Values are read when asked for, not at import, so tests and the eval runner
can point DATABASE_URL at a throwaway database.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

DEV = "dev"
DEV_DATABASE_URL = "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"
DEV_ADMIN_URL = "postgres://postgres:postgres@localhost:5432/postgres?sslmode=disable"


# The shortest INTERNAL_API_TOKEN accepted, as in the Go backend.
MIN_TOKEN_LENGTH = 32


class SettingsError(RuntimeError):
    """A required setting is missing. The message names the variable only."""


@dataclass(frozen=True)
class Settings:
    env: str  # MEDSUM_ENV, "" when unset
    database_url: str
    internal_api_token: str = field(repr=False)

    @property
    def is_dev(self) -> bool:
        return self.env == DEV


def _env(environ: Mapping[str, str]) -> str:
    return environ.get("MEDSUM_ENV", "").strip()


def _required(environ: Mapping[str, str], name: str, dev_default: str) -> str:
    value = environ.get(name, "").strip()
    if value:
        return value
    if _env(environ) == DEV:
        return dev_default
    raise SettingsError(
        f"{name} is not set. It is required unless MEDSUM_ENV=dev, which"
        " falls back to the local development database."
    )


def database_url(environ: Mapping[str, str] | None = None) -> str:
    """DATABASE_URL, or the local development database under MEDSUM_ENV=dev."""
    return _required(
        os.environ if environ is None else environ, "DATABASE_URL", DEV_DATABASE_URL
    )


def eval_admin_url(environ: Mapping[str, str] | None = None) -> str:
    """MEDSUM_EVAL_ADMIN_URL, or the local server under MEDSUM_ENV=dev."""
    return _required(
        os.environ if environ is None else environ,
        "MEDSUM_EVAL_ADMIN_URL",
        DEV_ADMIN_URL,
    )


def internal_api_token(environ: Mapping[str, str] | None = None) -> str:
    """INTERNAL_API_TOKEN; there is no development default."""
    environ = os.environ if environ is None else environ
    token = environ.get("INTERNAL_API_TOKEN", "").strip()
    if not token:
        raise SettingsError(
            "INTERNAL_API_TOKEN is not set. Set it in scripts/.env.local (see"
            " .env.example), the same as the Go backend's."
        )
    if len(token) < MIN_TOKEN_LENGTH:
        raise SettingsError(
            f"INTERNAL_API_TOKEN must be at least {MIN_TOKEN_LENGTH} characters."
        )
    return token


def load(environ: Mapping[str, str] | None = None) -> Settings:
    """Every setting the service needs to start; SettingsError if one is missing."""
    environ = os.environ if environ is None else environ
    return Settings(
        env=_env(environ),
        database_url=database_url(environ),
        internal_api_token=internal_api_token(environ),
    )
