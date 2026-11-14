"""
Settings: a missing database URL fails closed outside MEDSUM_ENV=dev, and
the service refuses to start without one.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import insights_service as service
import settings

SRC = Path(settings.__file__).resolve().parent
URL = "postgres://medsum_ai:secret@db.internal:5432/med_sum"
TOKEN = "0123456789abcdef0123456789abcdef"
WITH_TOKEN = {"INTERNAL_API_TOKEN": TOKEN}


@pytest.mark.parametrize("env", [{}, {"MEDSUM_ENV": "production"}, {"MEDSUM_ENV": ""}])
def test_a_missing_database_url_is_an_error_outside_dev(env):
    with pytest.raises(settings.SettingsError, match="DATABASE_URL is not set"):
        settings.database_url(env)
    with pytest.raises(settings.SettingsError):
        settings.load(env | WITH_TOKEN)


def test_a_blank_database_url_counts_as_missing():
    with pytest.raises(settings.SettingsError):
        settings.database_url({"DATABASE_URL": "  "})


def test_dev_falls_back_to_the_local_development_database():
    assert settings.database_url({"MEDSUM_ENV": "dev"}) == settings.DEV_DATABASE_URL
    loaded = settings.load({"MEDSUM_ENV": "dev"} | WITH_TOKEN)
    assert loaded.is_dev and loaded.database_url == settings.DEV_DATABASE_URL


@pytest.mark.parametrize("env", ["dev", "production", None])
def test_a_set_database_url_is_used_as_given(env):
    environ = {"DATABASE_URL": URL} | WITH_TOKEN | ({"MEDSUM_ENV": env} if env else {})
    assert settings.load(environ).database_url == URL


def test_the_eval_admin_url_follows_the_same_rule():
    with pytest.raises(settings.SettingsError, match="MEDSUM_EVAL_ADMIN_URL"):
        settings.eval_admin_url({})
    assert settings.eval_admin_url({"MEDSUM_ENV": "dev"}) == settings.DEV_ADMIN_URL
    assert settings.eval_admin_url({"MEDSUM_EVAL_ADMIN_URL": URL}) == URL


def test_the_error_names_the_variable_and_no_url():
    with pytest.raises(settings.SettingsError) as raised:
        settings.database_url({"MEDSUM_EVAL_ADMIN_URL": URL})
    assert "postgres" not in str(raised.value)


@pytest.mark.parametrize(
    "token", ["", "   ", "dev-internal-token", "x" * (settings.MIN_TOKEN_LENGTH - 1)]
)
def test_the_internal_token_is_required_and_long_even_in_dev(token):
    environ = {"MEDSUM_ENV": "dev", "INTERNAL_API_TOKEN": token}
    with pytest.raises(settings.SettingsError, match="INTERNAL_API_TOKEN") as raised:
        settings.load(environ)
    assert token.strip() == "" or token not in str(raised.value)


def test_the_token_is_kept_out_of_the_settings_repr():
    loaded = settings.load({"MEDSUM_ENV": "dev"} | WITH_TOKEN)
    assert loaded.internal_api_token == TOKEN and TOKEN not in repr(loaded)


def test_the_service_does_not_start_without_a_token(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("MEDSUM_WORKER", "0")

    with pytest.raises(settings.SettingsError, match="INTERNAL_API_TOKEN"):
        with TestClient(service.app):
            pass


def test_the_service_does_not_start_without_a_database_url(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", TOKEN)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("MEDSUM_ENV", raising=False)
    monkeypatch.setenv("MEDSUM_WORKER", "0")

    with pytest.raises(settings.SettingsError):
        with TestClient(service.app):
            pass


def test_the_service_starts_with_one(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)  # not connected to: no worker
    monkeypatch.setenv("INTERNAL_API_TOKEN", TOKEN)
    monkeypatch.setenv("MEDSUM_WORKER", "0")
    monkeypatch.setattr(service, "INTERNAL_API_TOKEN", "")

    with TestClient(service.app) as client:
        assert client.get("/health").json()["status"] == "ok"


def test_only_the_settings_module_holds_the_development_database_url():
    holders = [
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "postgres:postgres@" in path.read_text(encoding="utf-8")
    ]
    assert holders == ["settings.py"]


def test_the_service_is_started_on_localhost_only():
    # Only the Go backend on the same machine calls it; uvicorn's commands
    # in the run script and the README name the loopback address.
    root = SRC.parents[1]
    texts = [
        (root / "scripts" / "run.ps1").read_text(encoding="utf-8"),
        (root / "README.md").read_text(encoding="utf-8"),
    ]
    commands = [
        line for text in texts for line in text.splitlines()
        if "uvicorn insights_service:app" in line
    ]  # fmt: skip
    assert commands and all("--host 127.0.0.1" in line for line in commands)
