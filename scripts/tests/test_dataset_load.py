"""The dataset loader, against a fake backend (no network, no uploads)."""

import json

import httpx
import pytest

from dataset import load


class FakeBackend:
    def __init__(self, fail_upload: int | None = None):
        self.requests: list[httpx.Request] = []
        self.fail_upload = fail_upload
        self.uploads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/login":
            phone = json.loads(request.content)["phone_number"]
            return httpx.Response(200, json={"token": f"token-{phone}"})
        if request.url.path == "/documents":
            self.uploads += 1
            if self.uploads == self.fail_upload:
                return httpx.Response(415, text="file content does not match")
            return httpx.Response(201, json={"id": f"uuid-{self.uploads}"})
        return httpx.Response(404)


def client_for(backend) -> httpx.Client:
    return httpx.Client(
        base_url="http://backend", transport=httpx.MockTransport(backend)
    )


def test_each_patient_logs_in_once_and_uploads_in_order():
    backend = FakeBackend()
    patients, uploads = load.plan()

    created = load.load(client_for(backend))

    logins = [r for r in backend.requests if r.url.path == "/login"]
    assert sorted(json.loads(r.content)["phone_number"] for r in logins) == sorted(
        p["phone"] for p in patients.values()
    )
    for r in logins:
        body = json.loads(r.content)
        assert body["full_name"] == next(
            p["name"] for p in patients.values() if p["phone"] == body["phone_number"]
        )

    posted = [r for r in backend.requests if r.url.path == "/documents"]
    assert len(posted) == len(uploads)
    for request, upload in zip(posted, uploads):
        phone = patients[upload.patient]["phone"]
        assert request.headers["Authorization"] == f"Bearer token-{phone}"
        body = request.read()
        assert f'filename="{upload.file.name}"'.encode() in body
        assert f"Content-Type: {upload.content_type}".encode() in body
        assert upload.file.read_bytes() in body
    assert list(created) == [u.document for u in uploads]
    assert created[uploads[0].document] == "uuid-1"


def test_a_refused_upload_stops_the_load():
    backend = FakeBackend(fail_upload=2)

    with pytest.raises(httpx.HTTPStatusError):
        load.load(client_for(backend))

    assert backend.uploads == 2


def test_without_yes_nothing_is_sent(monkeypatch, capsys):
    def no_client(*args, **kwargs):
        raise AssertionError("a client was created")

    monkeypatch.setattr(load.httpx, "Client", no_client)

    load.main([])

    out = capsys.readouterr().out
    assert "Nothing uploaded" in out
    assert "d04" in out.splitlines()[0]
