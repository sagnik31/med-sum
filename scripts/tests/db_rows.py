"""Rows the database tests need before they can add documents."""

import uuid


def add_account(db, email: str = "clinician@hospital.test") -> str:
    """A staff account (staff_users row) in the test organisation; returns its id."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (name) VALUES ('Test Hospital') "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
        )
        (org,) = cur.fetchone()
        cur.execute(
            "INSERT INTO staff_users (org_id, email, full_name) VALUES (%s, %s, %s) "
            "RETURNING id",
            (org, email, "Dr Test"),
        )
        return str(cur.fetchone()[0])


def add_patient(
    db,
    email: str = "clinician@hospital.test",
    *,
    name: str = "Test Patient",
    mrn: str | None = None,
) -> str:
    """
    A patient record in the test organisation, created by the staff
    account with that email (made on first use); returns the patient's id.
    Each call is a new patient, with a fresh MRN unless one is given.
    """
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (name) VALUES ('Test Hospital') "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
        )
        (org,) = cur.fetchone()
        cur.execute(
            "INSERT INTO staff_users (org_id, email, full_name) VALUES (%s, %s, %s) "
            "ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id",
            (org, email, "Dr Test"),
        )
        (staff,) = cur.fetchone()
        cur.execute(
            "INSERT INTO patients (org_id, mrn, full_name, created_by)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (org, mrn or f"MRN-{uuid.uuid4().hex[:12]}", name, staff),
        )
        return str(cur.fetchone()[0])
