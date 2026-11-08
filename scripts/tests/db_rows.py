"""Rows the database tests need before they can add documents."""


def add_account(db, email: str = "clinician@hospital.test") -> str:
    """A staff account (users row) in the test organisation; returns its id."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (name) VALUES ('Test Hospital') "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
        )
        (org,) = cur.fetchone()
        cur.execute(
            "INSERT INTO users (org_id, email, full_name) VALUES (%s, %s, %s) "
            "RETURNING id",
            (org, email, "Dr Test"),
        )
        return str(cur.fetchone()[0])
