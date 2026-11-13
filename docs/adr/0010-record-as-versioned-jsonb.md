# 0010. One JSONB record per document, with a schema version

- **Status:** Accepted, 2026-11-07. Built.

## Context

Each document produces a structured record: patient details as printed, document details, lab results, medicines, stated conditions, remarks, vitals, allergies, the page transcriptions, and provenance on every item. The schema is still changing, and later document types will add primitives.

## Decision

- The record is one JSONB value per document in `document_records`, with a `schema_version` that must equal the JSON's own. Reprocessing replaces it.
- The record is defined once, as Pydantic models (`scripts/src/record/`). The parse schemas and the stored-record schema are exported from them as JSON Schema into `scripts/schemas/`, and a test fails when the committed files drift.
- Primitives are named after FHIR resources (`Observation`, `MedicationRequest`, `StatedCondition`, `Remark`, `Vital`, `Allergy`, ...). `Procedure`, `Encounter` and `ImagingFinding` are defined but not extracted, so later document types fit without a redesign.
- Every item carries `provenance`: the run, step, model and prompt that produced it, its review status, and the reasons it was flagged.

## Alternatives rejected

- **Normalised tables per primitive.** Many migrations while the schema is moving, for queries nobody runs yet.
- **FHIR resources as stored.** Heavier than needed, and implies codes the project deliberately doesn't assign (see [0009](0009-no-medical-codes-or-brand-mapping.md)).

## Consequences

- The Go API passes the record through and decodes only the citation and provenance parts, so record fields can change without a Go change.
- Cross-document queries (all creatinine values of a patient) happen in code over the records, not in SQL.

## What would change it

Query needs across many patients (cohorts, dashboards), or a schema stable enough that tables pay off.
