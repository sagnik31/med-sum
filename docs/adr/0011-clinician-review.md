# 0011. Clinician review: confirm or correct beside the reading, never over it

- **Status:** Accepted, 2026-11-14.

## Context

The pipeline sends a document to `needs_review` when a deterministic signal fires (ADR 0004). Until now nothing could be done about it: the document page showed the reasons and the patient summary marked the values "awaiting review", for ever. A clinician has to be able to look at the original upload, say "this is right" or "this is the right value", and have the summary use the answer. The answer is a clinical act, so it must say who gave it and when, and it must never destroy what the model read: that reading is the evidence of what the pipeline did, and of how often each signal was right.

## Decision

**A review is stored inside the record, next to what it reviews.** Each item (and the patient and document header values) has a provenance with a status. Confirming an item sets its status to `clinician_confirmed`; correcting a field appends a `Correction` (field, the model's reading, the new value, the clinician's name, the time, an optional note) and sets `clinician_corrected`. Both set `reviewed_by` and `reviewed_at`. `printed` and `derived` are never changed, and the low-confidence reasons stay, so it is known afterwards what the review answered. Any item can be reviewed, flagged or not; there is no second approver.

**The reviewed view is computed, not stored** (`scripts/src/record/review.py`, `reviewed_view`): each corrected field replaced by its latest correction, and the item's derived values computed again from it by the same code that built them (a corrected result gets its number and flag again; a corrected report date moves the document date). The patient summary and the document page show this view. The summary marks each corrected value "corrected by Dr X on date"; the document page also says what was read.

**A corrected value goes through the checks that apply to a value on its own**: units and ranges, printed flag against computed flag, dates. It is never checked against the transcription (the source-line, text-layer and range-bound checks): the clinician read the original upload, not the transcription. What the checks find is shown to the clinician, who must acknowledge it to save; it never refuses the value, since the document itself may be wrong and is never "corrected" by code (ADR 0004).

**A document is verified** once nothing in it is low confidence. Its page is built again from the reviewed view, and its patient's summary is rebuilt.

**Who does what.** The Go API owns the write: it checks the permission (`review.write`) and the organisation, loads the record, asks the AI service to apply the review (`POST /internal/review-apply`, a pure function: record in, reviewed record, checks and page out; it reads and writes nothing), then stores the new record, a row of `record_reviews` (who, when, which item and field, the signal ids the item carried, whether it was an audit-sample review) and the audit event in one transaction. The record's `updated_at` guards against two clinicians saving over each other (409). A document being processed can't be reviewed.

**The review shows the original upload**, not page images rendered again: `GET /documents/{id}/file` (permission `document.read`, organisation-scoped like every document route, audited as `document_file_viewed` before a byte is sent) serves the stored file with the type its extension had when the upload checked it against the file's first bytes, under a generic file name. Images show directly; a PDF opens in the browser's own viewer at the page being checked (`#page=N`). It is the one API response that may be framed: its content security policy names the app's origins in `frame-ancestors` and drops `X-Frame-Options`, and still allows no script and no load from elsewhere. Every other response, its refusals included, keeps `frame-ancestors 'none'` (ADR 0010).

**Reprocessing** replaces the record, so its reviews start again; the `record_reviews` rows of the earlier run stay.

**The record's schema version stays 1.** `SourceDocument` gained an optional `corrections` list with a default, which every stored record reads the same without; the version is bumped when a field is removed, renamed, made required or changes meaning.

## Alternatives rejected

- **Overwriting `printed` with the correction.** The record would then claim the model read a value it never read, and nothing would be left to measure the signals with.
- **A separate table of corrections applied when pages are built.** Two places to read a value from; every consumer (summary, document page, tools, exports) would have to join them. Inside the record, one reader (`reviewed_view`) serves all of them.
- **Running every validator on a corrected value.** The transcription checks would flag every correction of a misread value, which is the point of correcting it.
- **Refusing a corrected value that fails a check.** A printed flag that disagrees with the right value is the document's problem; the clinician decides.
- **Page images rendered again from the PDF for the review.** A second copy of what the browser can show from the file itself, and a rendering bug would put a wrong image beside the values.
- **A second approver.** Not how a ward works for this kind of check; the trail says who did it.
- **The AI service writing the review.** It would need write access to the audit log, and the audit row would no longer share the write's transaction.

## Consequences

- The summary is only as current as its last rebuild: it is rebuilt when a review leaves a document verified, not after every correction of a document still awaiting review (whose values are still marked awaiting review there).
- Correction text is clinical data inside the record; the audit row holds the item id and field name only (ADR 0008).
- A correction of a test's name can move a remark off that test; the reviewed view links remarks again.

## What would change it

Real use showing clinicians want to correct structure (split one row into two, add a value the model dropped) rather than fields: that needs adding items, which this design doesn't do. A regulator requiring a second signature on corrections.
