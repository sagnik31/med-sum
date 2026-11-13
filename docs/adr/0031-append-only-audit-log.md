# 0031. An append-only audit log of every patient-data read and write

- **Status:** Accepted, 2026-11-08; confirmed 2026-11-13. Not built yet; the README says there is no audit log.

## Context

A records system must be able to answer "who viewed or changed this patient's data, and when". India's DPDP Act 2023 and NABH-style hospital audits expect it.

## Decision

- An `audit_events` table: time, organisation, actor, action, target type and id, patient id, outcome, IP, user agent, and small JSON details. **Never clinical content.**
- **Append-only:** a trigger rejects UPDATE and DELETE.
- One row per security-relevant event: login success and failure, lockout, logout, staff changes, and **every read and write of patient data** (patient viewed, document uploaded, viewed, deleted or reprocessed, summary generated or viewed, a field accepted or corrected).
- Mutations write their audit row in the same transaction. Reads write it before the data is returned, and **fail closed** (500) if they can't.
- One helper writes the rows, called from the handlers. Admins read the log through `audit.read`.

## Alternatives rejected

- **Application logs as the audit trail.** Mutable, rotated, and without the guarantee that a read was recorded before data left.
- **Audit writes after the response.** A crash between the two would leave access unrecorded.

## Consequences

- Covered, once built: untraceable access.
- Every patient-data route gets a little slower (one insert), and an audit failure blocks access rather than letting it through unrecorded.

## What would change it

A central audit service in a real deployment; the same rules would apply to it.
