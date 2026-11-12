"""The AI service's routes: internal token checks and job requests (no database)."""

import pytest
from fastapi.testclient import TestClient

import insights_service as service

TOKEN = "test-internal-token"


@pytest.fixture
def scheduled(monkeypatch):
    """Record document requests instead of queueing them."""
    requests = []

    def request(document_id, reprocess=False):
        requests.append((document_id, reprocess) if reprocess else document_id)
        return "queued"

    monkeypatch.setattr(service, "request_document", request)
    return requests


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(service, "INTERNAL_API_TOKEN", TOKEN)
    return TestClient(service.app)


def test_health_needs_no_token(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["model_health"]) == {"failures_to_open", "cooldown_s", "models"}


def test_a_document_job_is_scheduled_with_the_right_token(client, scheduled):
    response = client.post(
        "/internal/generate-insights",
        json={"document_id": "doc-1"},
        headers={"X-Internal-Token": TOKEN},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "accepted", "document_id": "doc-1", "result": "queued"
    }  # fmt: skip
    assert scheduled == ["doc-1"]


def test_a_reprocess_is_requested_as_one(client, scheduled):
    response = client.post(
        "/internal/reprocess-document",
        json={"document_id": "doc-1"},
        headers={"X-Internal-Token": TOKEN},
    )

    assert response.status_code == 200
    assert response.json()["result"] == "queued"
    assert scheduled == [("doc-1", True)]


@pytest.mark.parametrize(
    "path, body",
    [
        ("/internal/generate-insights", {"document_id": "doc-1"}),
        ("/internal/reprocess-document", {"document_id": "doc-1"}),
        ("/internal/generate-user-insights", {"user_id": "user-1"}),
    ],
)
@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Internal-Token": "wrong"}, {"X-Internal-Token": ""}],
    ids=["missing", "wrong", "empty"],
)
def test_internal_routes_reject_a_bad_token(client, scheduled, path, body, headers):
    response = client.post(path, json=body, headers=headers)

    assert response.status_code == 401
    assert scheduled == []


def test_internal_routes_are_closed_when_no_token_is_configured(
    client, scheduled, monkeypatch
):
    monkeypatch.setattr(service, "INTERNAL_API_TOKEN", "")

    response = client.post(
        "/internal/generate-insights",
        json={"document_id": "doc-1"},
        headers={"X-Internal-Token": ""},
    )

    assert response.status_code == 503
    assert scheduled == []


def test_a_request_without_a_document_id_is_rejected(client, scheduled):
    response = client.post(
        "/internal/generate-insights", json={}, headers={"X-Internal-Token": TOKEN}
    )

    assert response.status_code == 422
    assert scheduled == []
