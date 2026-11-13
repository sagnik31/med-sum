# 0001. Extract and display for hospital staff; never infer

- **Status:** Accepted, 2026-11-07 and 2026-11-08; scope settled 2026-11-13. Built, except patients as records of their own: until then each document belongs to the account that uploaded it.

## Context

The first versions had a model write the summaries, and they invented things: a remark attached to the wrong test, "acute kidney injury" from one creatinine value, "worsening" from one data point, "high confidence" from one report, a timeline out of order, a lipid status guessed from a statin. A 4B-parameter local model is not a medical-grade reasoner, and a doctor can't tell an invented sentence from a real one. The first versions also let patients upload their own documents, which raises questions of consent, sharing and identity that this project doesn't set out to answer.

## Decision

**What the system does.** It extracts what is printed on a document and displays it. Nothing is inferred, interpreted or made up, and every value shown cites the page and lines it came from.

- The model reads documents and fills in schemas. It never writes a diagnosis, a judgement, a severity or a trend.
- Code computes what can be computed without guessing (numbers, flags from printed ranges, dates, unit conversions, trends) and marks it as derived ([0003](0003-printed-and-derived-values.md)).
- Pages are built by code; the one piece of model prose, the summary's overview, is checked against the computed facts and dropped if it says anything they don't ([0007](0007-pages-built-by-code.md)).
- Everything printed that makes sense to keep is extracted, vitals, allergies and the printed patient details included, and lines no item uses are listed, so nothing is lost silently.
- **No medical codes and no brand-to-generic mapping.** Medicines stay as printed (a brand stays a brand), and no LOINC, SNOMED, ICD or RxNorm codes are assigned: a small model's mapping would be wrong now and then while looking authoritative. The record's parts are named after FHIR resources so a later mapping is easy, but it is not FHIR.

**Which documents.** Lab reports and prescriptions are parsed. Anything else is classified `other`, keeps its transcription and goes to review unparsed. The record is designed so other types (discharge summaries, imaging reports) fit later without a redesign.

**Who uses it.**

- **Clinicians** find or create patients, upload their documents, read document pages and patient summaries, review and correct flagged values, and ask for reprocessing. **Admins** manage staff accounts and the audit log and see no clinical data. A person who needs both holds both roles ([0010](0010-staff-access-and-security.md)).
- **Patients have no accounts.** A patient is a record: name, date of birth (or age when that is all that is known), sex, and the hospital's patient ID (MRN), which is required and unique within an organisation.
- Clinicians upload through the UI only; hospital systems don't call the API.
- **One organisation**, in which every clinician sees every patient. Every patient, account and audit row still carries `org_id`, so separating hospitals later is a query change.
- A clinician's registration number is stored as entered and **not verified**; the README says so.

## Alternatives rejected

- **A model-written summary with a stricter prompt.** Prompts reduce invention; they don't prevent it, and nothing checks the result.
- **Clinical interpretation by the model** (organ-system groupings, "abnormal", likely diagnoses). That is the doctor's job, and a small model's opinion would be read as the system's.
- **Model-generated codes or generic names, or a licensed terminology database.** The first is unverifiable; the second is a paid dependency, and free sources for Indian brands are incomplete.
- **Patient self-service, per-patient access lists, an API for hospital systems.** Out of scope for a first clinical version; the organisation column keeps the door open.

## Consequences

- The summary is less fluent than a model-written one and says less. It never says something the documents don't.
- Every new field is checked against the rule: printed on a document, or computed from printed values by stated rules.
- Two brands of one medicine aren't recognised as the same. There is no break-glass access or per-patient restriction.

## What would change it

A later clinical-support phase adding interpretation, as clearly separated, labelled output; a reliable licensed terminology with a deterministic lookup; several hospitals or departments that must not see each other's patients.
