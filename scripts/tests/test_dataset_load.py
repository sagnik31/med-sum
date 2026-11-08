"""The dataset loader, against a fake backend (no network, no uploads)."""

import json

import httpx
import pytest

from dataset import load

PASSWORD = "a long dev password"


class FakeBackend:
    def __init__(self, fail_upload: int | None = None):
        self.requests: list[httpx.Request] = []
        self.fail_upload = fail_upload
        self.uploads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("X-Requested-With") != "med-sum":
            return httpx.Response(403, text="missing X-Requested-With header")
        if request.url.path == "/auth/login":
            body = json.loads(request.content)
            if body["password"] != PASSWORD:
                return httpx.Response(401, text="invalid email or password")
            return httpx.Response(
                200,
                json={"email": body["email"]},
                headers={
                    "Set-Cookie": f"medsum_session=session-{body['email']}; "
                    "Path=/; HttpOnly; SameSite=Strict"
                },
            )
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

    created = load.load(client_for(backend), PASSWORD)

    logins = [r for r in backend.requests if r.url.path == "/auth/login"]
    assert sorted(json.loads(r.content)["email"] for r in logins) == sorted(
        f"patient-{key}@med-sum.test" for key in patients
    )

    posted = [r for r in backend.requests if r.url.path == "/documents"]
    assert len(posted) == len(uploads)
    for request, upload in zip(posted, uploads):
        email = load.account_email(upload.patient)
        # Exactly this patient's session, never another's from the jar.
        assert request.headers["Cookie"] == f"medsum_session=session-{email}"
        body = request.read()
        assert f'filename="{upload.file.name}"'.encode() in body
        assert f"Content-Type: {upload.content_type}".encode() in body
        assert upload.file.read_bytes() in body
    assert list(created) == [u.document for u in uploads]
    assert created[uploads[0].document] == "uuid-1"


def test_uploads_interleave_patients():
    # The upload order mixes patients on purpose, so the session switching
    # in the test above is really exercised.
    _, uploads = load.plan()
    switches = sum(a.patient != b.patient for a, b in zip(uploads, uploads[1:]))
    assert switches > len({u.patient for u in uploads})


def test_a_wrong_password_stops_the_load():
    backend = FakeBackend()

    with pytest.raises(httpx.HTTPStatusError):
        load.load(client_for(backend), "not the password")

    assert backend.uploads == 0


def test_a_refused_upload_stops_the_load():
    backend = FakeBackend(fail_upload=2)

    with pytest.raises(httpx.HTTPStatusError):
        load.load(client_for(backend), PASSWORD)

    assert backend.uploads == 2


def test_without_yes_nothing_is_sent(monkeypatch, capsys):
    def no_client(*args, **kwargs):
        raise AssertionError("a client was created")

    monkeypatch.setattr(load.httpx, "Client", no_client)

    load.main([])

    out = capsys.readouterr().out
    assert "Nothing uploaded" in out
    assert "d04" in out.splitlines()[0]


def test_yes_needs_the_dev_password(monkeypatch):
    monkeypatch.delenv("MEDSUM_DEV_PASSWORD", raising=False)
    monkeypatch.setattr(
        load.httpx, "Client", lambda *a, **k: pytest.fail("a client was created")
    )

    with pytest.raises(SystemExit, match="MEDSUM_DEV_PASSWORD"):
        load.main(["--yes"])
