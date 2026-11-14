"""The dataset loader, against a fake backend (no network, no uploads)."""

import json

import httpx
import pytest

from dataset import load

PASSWORD = "a long dev password"


class FakeBackend:
    """The backend's login, patient and upload routes, in memory."""

    def __init__(self, fail_upload: int | None = None, existing: dict | None = None):
        self.requests: list[httpx.Request] = []
        self.fail_upload = fail_upload
        self.uploads = 0
        self.patients = dict(existing or {})  # MRN -> id

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if (
            request.method != "GET"
            and request.headers.get("X-Requested-With") != "med-sum"
        ):
            return httpx.Response(403, text="missing X-Requested-With header")
        if path == "/auth/login":
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
        if (
            request.headers.get("Cookie")
            != f"medsum_session=session-{load.CLINICIAN_EMAIL}"
        ):
            return httpx.Response(401)
        if path == "/patients" and request.method == "POST":
            body = json.loads(request.content)
            if body["mrn"] in self.patients:
                return httpx.Response(
                    409, text="a patient with this MRN already exists"
                )
            self.patients[body["mrn"]] = f"patient-{len(self.patients) + 1}"
            return httpx.Response(201, json={"id": self.patients[body["mrn"]], **body})
        if path == "/patients" and request.method == "GET":
            q = request.url.params["q"]
            return httpx.Response(
                200,
                json=[
                    {"id": i, "mrn": m}
                    for m, i in self.patients.items()
                    if m.startswith(q)
                ],
            )
        if path.startswith("/patients/") and path.endswith("/documents"):
            self.uploads += 1
            if self.uploads == self.fail_upload:
                return httpx.Response(415, text="file content does not match")
            return httpx.Response(201, json={"id": f"uuid-{self.uploads}"})
        return httpx.Response(404)


def client_for(backend) -> httpx.Client:
    return httpx.Client(
        base_url="http://backend", transport=httpx.MockTransport(backend)
    )


def test_the_clinician_creates_each_patient_and_uploads_to_it_in_order():
    backend = FakeBackend()
    patients, uploads = load.plan()

    created = load.load(client_for(backend), PASSWORD)

    logins = [r for r in backend.requests if r.url.path == "/auth/login"]
    assert [json.loads(r.content)["email"] for r in logins] == [load.CLINICIAN_EMAIL]

    made = [json.loads(r.content) for r in backend.requests
            if r.url.path == "/patients" and r.method == "POST"]  # fmt: skip
    assert made == [
        {"mrn": p["mrn"], "full_name": p["name"], "date_of_birth": p["date_of_birth"],
         "sex": p["sex"]}
        for p in patients.values()
    ]  # fmt: skip

    posted = [r for r in backend.requests if r.url.path.endswith("/documents")]
    assert len(posted) == len(uploads)
    for request, upload in zip(posted, uploads):
        mrn = patients[upload.patient]["mrn"]
        assert request.url.path == f"/patients/{backend.patients[mrn]}/documents"
        body = request.read()
        assert f'filename="{upload.file.name}"'.encode() in body
        assert f"Content-Type: {upload.content_type}".encode() in body
        assert upload.file.read_bytes() in body
    assert list(created) == [u.document for u in uploads]
    assert created[uploads[0].document] == "uuid-1"


def test_a_second_load_finds_the_patients_by_mrn():
    patients, uploads = load.plan()
    existing = {p["mrn"]: f"old-{p['key']}" for p in patients.values()}
    backend = FakeBackend(existing=existing)

    load.load(client_for(backend), PASSWORD)

    assert backend.patients == existing
    first = next(r for r in backend.requests if r.url.path.endswith("/documents"))
    assert first.url.path == f"/patients/old-{uploads[0].patient}/documents"


def test_uploads_interleave_patients():
    # The upload order mixes patients on purpose, so uploads go to the right
    # record as the patient changes.
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
