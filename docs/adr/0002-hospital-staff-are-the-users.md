# 0002. Hospital staff are the users; patients are records

- **Status:** Accepted, 2026-11-08; scope settled 2026-11-13. Staff accounts and roles are built. Patients as records of their own are not built yet: until they are, each document belongs to the account that uploaded it, and each synthetic patient has a development account of its own.

## Context

A summary of a patient's documents is useful to the doctor who sees the patient. Letting patients upload and manage their own documents raises different questions (consent, sharing, support, identity checks) that this project doesn't set out to answer.

## Decision

- **Clinicians** (licensed medical professionals at the hospital) are the users who work with patient data: they find or create patients, upload their lab reports and prescriptions, read document pages and patient summaries, review and correct flagged values, and ask for reprocessing.
- **Admins** (hospital IT or an administrator) create and disable staff accounts, assign roles, issue password-reset links and read the audit log. They see no clinical data (see [0030](0030-authorization-in-two-layers.md)). A person who needs both holds both roles.
- **Patients have no accounts.** A patient is a record: name, date of birth (or age when that is all that is known), sex, and the hospital's patient ID (MRN). The MRN is required and unique within an organisation, which also strengthens the check that a document's printed patient matches the patient it was uploaded to.
- Documents are uploaded by clinicians through the UI only. Hospital systems don't call the API.
- **One organisation**, in which every clinician sees every patient. Every patient, staff account and audit row still carries `org_id`, so separating hospitals later is a query change, not a redesign.
- A clinician's registration number is stored as entered by the admin. It is **not verified**, and the README says so.

## Alternatives rejected

- **Patient self-service** (what the first version did: each user uploaded their own documents). Out of scope: the project is a clinical support tool.
- **Per-patient access lists or care teams.** Needed in a large hospital, but not for one organisation in a first version. The organisation column keeps the door open.
- **An API for hospital systems.** Adds service accounts and integration work with no one to integrate with.

## Consequences

- Authorization is about staff and permissions, not ownership of one's own data.
- No break-glass access or per-patient restrictions exist; the README lists this as a limitation.

## What would change it

A real deployment: several hospitals sharing the system, departments that must not see each other's patients, or a need for patients to see their own records.
