"""
Upload the synthetic dataset through the backend's normal routes: log in
as each patient's development account, then upload their documents in the
manifest's upload order. Run from scripts/src:

    python -m dataset.load            # show what would be uploaded
    python -m dataset.load --yes      # upload

Uploading starts processing on the AI service at once, so --yes means
live model runs (minutes per page on a CPU) and writes to whatever
database the backend uses.

The accounts come from `medsumctl seed-dev` (backend/cmd/medsumctl), with
the password in MEDSUM_DEV_PASSWORD. Until patients are records of their
own, each synthetic patient's documents are
uploaded by an account of its own, patient-<key>@med-sum.test.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from dataset.build import DATASET_DIR, account_email

SESSION_COOKIE = "medsum_session"
# The backend refuses state-changing requests without it (CSRF check).
CSRF_HEADERS = {"X-Requested-With": "med-sum"}


@dataclass
class Upload:
    document: str  # dataset document id, e.g. d04
    patient: str
    file: Path
    content_type: str


def plan(dataset_dir: Path = DATASET_DIR) -> tuple[dict, list[Upload]]:
    """The patients by key, and the uploads in order."""
    manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    patients = {p["key"]: p for p in manifest["patients"]}
    documents = {d["id"]: d for d in manifest["documents"]}
    uploads = []
    for doc_id in manifest["upload_order"]:
        doc = documents[doc_id]
        uploads.append(
            Upload(
                doc_id, doc["patient"], dataset_dir / doc["file"], doc["content_type"]
            )
        )
    return patients, uploads


def log_in(client: httpx.Client, email: str, password: str) -> str:
    """Log in and return the session cookie's value."""
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=CSRF_HEADERS,
    )
    response.raise_for_status()
    session = response.cookies.get(SESSION_COOKIE)
    if not session:
        raise RuntimeError(f"logging in as {email} set no session cookie")
    # Sessions are sent explicitly per request, so the client's own cookie
    # jar must not carry one patient's session into another's upload.
    client.cookies.clear()
    return session


def load(
    client: httpx.Client, password: str, dataset_dir: Path = DATASET_DIR
) -> dict[str, str]:
    """Upload everything; returns dataset id -> the backend's document id."""
    _, uploads = plan(dataset_dir)
    sessions: dict[str, str] = {}
    created: dict[str, str] = {}
    for upload in uploads:
        if upload.patient not in sessions:
            sessions[upload.patient] = log_in(
                client, account_email(upload.patient), password
            )
        response = client.post(
            "/documents",
            headers={
                **CSRF_HEADERS,
                "Cookie": f"{SESSION_COOKIE}={sessions[upload.patient]}",
            },
            files={
                "file": (
                    upload.file.name,
                    upload.file.read_bytes(),
                    upload.content_type,
                )
            },
        )
        response.raise_for_status()
        created[upload.document] = response.json()["id"]
    return created


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--yes", action="store_true", help="really upload")
    args = parser.parse_args(argv)

    patients, uploads = plan()
    for upload in uploads:
        name = patients[upload.patient]["name"]
        print(f"{upload.document}  {name:<15}  {upload.file.name}")
    if not args.yes:
        print(
            "\nNothing uploaded. Uploading starts live model runs on the AI service "
            "and writes to the backend's database; pass --yes to go ahead."
        )
        return
    password = os.environ.get("MEDSUM_DEV_PASSWORD")
    if not password:
        sys.exit(
            "MEDSUM_DEV_PASSWORD is not set (the password medsumctl seed-dev used)"
        )
    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        for doc_id, backend_id in load(client, password).items():
            print(f"uploaded {doc_id} as {backend_id}")


if __name__ == "__main__":
    main()
