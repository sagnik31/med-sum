# 0004. Deterministic checks decide what a clinician must review

- **Status:** Accepted, 2026-11-07 (validators) and 2026-11-09 (signals); checks added 2026-11-13. Built.

## Context

The pipeline's safety rests on catching what the model gets wrong. A model judging a model's output is neither repeatable nor calibrated at 4B parameters, and a threshold on a confidence score can't be chosen without measured data. Plain code can check a record against the text it cites, every time, in milliseconds. "Low confidence" also has to mean the same thing to the router, the reviewer and the evals.

## Decision

**Validators are code** (`scripts/src/validators/`), never a model. They return findings (validator, severity, message, item, field, page, lines):

1. **Source:** every value is on its cited lines as whole tokens after Unicode (NFKC) and whitespace normalisation, case-sensitive ("10" is not in "10.4"). A quote may be the whole cited line or an exact whole-token part of it.
2. **Text layer:** where the PDF has one, each value is also in PyMuPDF's text of the page, **in its own row**: near the item's test or medicine name, in a window as wide as its cited row, with whitespace removed and the item's own reference ranges taken out. This is the only check that sees a vision misread. Images have no such check, and that risk is accepted; a second model reading every page was judged too slow.
3. **Units and ranges** consistent; **3b:** a value equal to one of its own range bounds, printed nowhere else on its row, was probably read from the range.
4. **Flags:** printed against computed.
5. **Remark placement** by position.
6. **Medicines:** every medicine line in the record and every record medicine on a line; **6b:** every data row of a lab results table cited by a result.
7. **Dates** real and consistent. 8. **Coverage:** lines no item uses, shown only. 9. **Patient match:** the printed patient against the one the document was uploaded to.

**Severity decides the route.** An **error** means the parse may be wrong, so it goes to repair (1, 5, 6, 6b). A **flag** means the document itself is in doubt, which re-parsing can't change, so it goes to a clinician (2, 3, 3b, 4, 7, 9). An **info** finding is shown in review only.

**Low confidence is a fixed list of signals** (`SIGNALS` in `scripts/src/pipeline/signals.py`), each with an id, a level (field or document) and the reason shown to the reviewer. Reasons are stored as `"LC-nn: <reason>"` or `"LC-nn [field]: <reason>"`, so they are machine-readable and the wording comes from one table.

- **Routing signals are deterministic** (LC-01 to LC-18): an unreadable line, a page not fully read, a text-layer disagreement, a value or medicine not in the source after repair and the fallback, a dropped medicine or results row, a misplaced remark, a parse that never fit, inconsistent units, a flag mismatch, an impossible date, an undated document, a wrong patient, type `other`, a classifier disagreement, a value taken from its own range, a prompt possibly cut to fit the context. Any one ends the document in `needs_review`.
- An error becomes a signal only if it survives repair and the fallback; a flag is a signal at once; info never routes.
- **Operational signals** (fallback used, repairs needed, a model switched off or on) describe the run, are stored and shown, and never route.
- **Model-judged signals** (an LLM-as-a-judge score, the two models disagreeing) have a slot but are recorded only. Promoting one to routing is a new decision that cites eval results calibrating it.

## Alternatives rejected

- **An LLM as the judge, or the model's own confidence.** Not repeatable, not calibrated, and any threshold would be arbitrary.
- **Fuzzy value matching.** Would let "10.4" pass for "10".
- **Comparing with the text layer per page.** A misread that matched a number elsewhere on the page passed; fault injection showed it.
- **Routing on fallback use.** The fallback's output is judged on its merits by the same checks.

## Consequences

- Every flagged value has a named reason a clinician can read and an eval can count. The checks are measured as detectors ([0009](0009-measured-evals.md)), so their blind spots are known by count: misreads on pages without a text layer, a one-letter value matching inside another cell, results printed as lines rather than a table row.
- Findings quote document text, so they are masked before they are stored on telemetry rows ([0008](0008-patient-data-protection.md)).

## What would change it

A fault class the checks keep missing, a clean document they keep flagging, review outcomes showing a signal is mostly false alarms, or eval data calibrating a model-judged signal.
