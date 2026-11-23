"""
How well the low-confidence signals do in use, from clinicians' reviews:
the precision of each signal (of the values it raised, how many a
clinician corrected) and, from the audit samples, how often a checks_passed
document still held a wrong value (the escape rate). Read from the views
review_signal_precision and audit_sample_outcomes, which hold counts and
ids only. See docs/adr/0011-clinician-review.md.

    python -m evals.review_signals [--database-url URL] [--org ORG_ID]

A signal's precision is corrected / reviewed. A confirmed value was a
false alarm: the reading was right and the clinician's time went on
checking it. Precision says nothing about what a signal missed; that is
the escape rate's job, and the audit sample is the only place a miss can
be seen.
"""

import argparse
import sys
from dataclasses import dataclass

import psycopg

from evals.stats import Interval, wilson
from pipeline import checkpoints
from pipeline.signals import SIGNALS


@dataclass(frozen=True)
class SignalPrecision:
    signal: str
    reviewed: int
    corrected: int
    confirmed: int

    @property
    def precision(self) -> Interval:
        return wilson(self.corrected, self.reviewed)


@dataclass(frozen=True)
class EscapeRate:
    finished: int  # audits finished
    escaped: int  # of those, the ones a clinician corrected a value in
    open: int  # audits still waiting

    @property
    def rate(self) -> Interval:
        return wilson(self.escaped, self.finished)


def precision(conn, org_id: str | None = None) -> list[SignalPrecision]:
    """Each signal's counts, summed over organisations unless one is given."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT signal, sum(reviewed)::int, sum(corrected)::int,"
            " sum(confirmed)::int FROM review_signal_precision"
            " WHERE %(org)s::uuid IS NULL OR org_id = %(org)s::uuid"
            " GROUP BY signal ORDER BY signal",
            {"org": org_id},
        )
        return [SignalPrecision(*row) for row in cur.fetchall()]


def escape_rate(conn, org_id: str | None = None) -> EscapeRate:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE finished),"
            " count(*) FILTER (WHERE finished AND escaped),"
            " count(*) FILTER (WHERE NOT finished)"
            " FROM audit_sample_outcomes"
            " WHERE %(org)s::uuid IS NULL OR org_id = %(org)s::uuid",
            {"org": org_id},
        )
        return EscapeRate(*cur.fetchone())


def report(rows: list[SignalPrecision], audits: EscapeRate) -> str:
    """The two results as Markdown."""
    out = ["# Low-confidence signals in review", ""]
    if rows:
        out += [
            "| Signal | What it says | Reviewed | Corrected | Confirmed | Precision |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in rows:
            reason = SIGNALS[r.signal].reason if r.signal in SIGNALS else "(unknown)"
            out.append(
                f"| {r.signal} | {reason} | {r.reviewed} | {r.corrected}"
                f" | {r.confirmed} | {r.precision.text()} |"
            )
    else:
        out.append("No reviewed values yet.")
    out += ["", "## Audit sample", ""]
    if audits.finished:
        out.append(
            f"Escape rate: {audits.rate.text()} of finished audits held a value a"
            " clinician corrected."
        )
    else:
        out.append("No finished audits yet.")
    if audits.open:
        out.append(f"{audits.open} audits are still waiting in the review queue.")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.review_signals")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--org", default=None, help="one organisation's id")
    args = parser.parse_args(argv)
    url = args.database_url or checkpoints.database_url()
    with psycopg.connect(url, autocommit=True) as conn:
        text = report(precision(conn, args.org), escape_rate(conn, args.org))
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
