# 0003. Extract and display; never infer

- **Status:** Accepted, 2026-11-07.

## Context

The first versions had a model write the summaries. They invented things: a remark attached to the wrong test, "acute kidney injury" from a single creatinine value, "worsening" from one data point, "high confidence" from one report, a timeline out of order, a lipid status guessed from a statin. A 4B-parameter local model is not a medical-grade reasoner, and a doctor can't tell an invented sentence from a real one.

## Decision

Med-Sum extracts what is printed on a document and displays it. Nothing is inferred, interpreted or made up. Every value shown traces back to a page and lines of a document.

- The model reads documents and fills in schemas. It never writes a diagnosis, a judgement, a severity or a trend.
- Code computes what can be computed without guessing (numbers, flags from printed ranges, dates, unit conversions, trends) and marks it as derived (see [0006](0006-printed-and-derived-values.md)).
- Pages are built by code from the records (see [0020](0020-pages-built-by-code.md)). The only model-written prose, the summary's short overview, is checked against the computed facts and dropped if it says anything they don't (see [0021](0021-patient-summary-rules.md)).
- Everything printed that makes sense to keep is extracted (vitals, allergies, the patient details in the header), and printed lines that no extracted item uses are listed, so nothing is lost silently.

## Alternatives rejected

- **A model-written summary with a stricter prompt.** Prompts reduce invention; they don't prevent it, and nothing checks the result.
- **Clinical interpretation by the model** (organ-system groupings, "abnormal", likely diagnoses). That is the doctor's job, and a small model's opinion would be read as the system's.

## Consequences

- The summary is less fluent than a model-written one, and says less. It never says something the documents don't.
- Every feature is checked against this rule: a new field must be printed on a document or computed from printed values by stated rules.

## What would change it

A later clinical-support phase could add interpretation, but only as clearly separated, labelled output, never mixed into the extracted record.
