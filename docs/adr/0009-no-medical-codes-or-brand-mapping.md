# 0009. No medical codes and no brand-to-generic mapping

- **Status:** Accepted, 2026-11-07.

## Context

Indian prescriptions mostly print brand names ("Telma 40", "Glycomet"). Mapping brands to generics, or tests to LOINC, diagnoses to SNOMED or ICD and medicines to RxNorm, would make records comparable across hospitals. The available models are small local models, not medical grade, and would hallucinate mappings that look authoritative.

## Decision

- Medicines are kept as printed: a brand stays a brand. No generic name is added.
- No LOINC, SNOMED, ICD or RxNorm codes are assigned.
- The record's primitives are named after FHIR resources (`Observation`, `MedicationRequest`, ...) so a later mapping is easy, but the record is not FHIR and carries no codes.
- The README states the limitation.

## Alternatives rejected

- **Model-generated mappings.** A wrong generic or code is worse than none, since it looks verified.
- **A licensed drug or terminology database.** No paid services in this project, and the curated free sources for Indian brands are incomplete.

## Consequences

- A doctor sees what the prescriber wrote.
- The summary can't tell that two brands are the same medicine.

## What would change it

A reliable, licensed terminology source and a deterministic lookup (not a model), checked like any other derived value.
