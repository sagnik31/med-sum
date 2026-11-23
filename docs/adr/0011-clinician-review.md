# 0011. Clinician review: confirm or correct beside the reading, never over it

- **Status:** Accepted, 2026-11-14; acknowledgement by name 2026-11-21. Amended 2026-11-23 by [0013](0013-model-choice-after-the-first-live-evals.md): no run ends verified; a document that passes every check is `checks_passed` and is verified only by a clinician's sign-off, and the audit samples are drawn from those documents.

## Context

The pipeline sends a document to `needs_review` when a deterministic signal fires (ADR 0004). Until now nothing could be done about it: the document page showed the reasons and the patient summary marked the values "awaiting review", for ever. A clinician has to be able to look at the original upload, say "this is right" or "this is the right value", and have the summary use the answer. The answer is a clinical act, so it must say who gave it and when, and it must never destroy what the model read: that reading is the evidence of what the pipeline did, and of how often each signal was right.

## Decision

**A review is stored inside the record, next to what it reviews.** Each item (and the patient and document header values) has a provenance with a status. Confirming an item sets its status to `clinician_confirmed`; correcting a field appends a `Correction` (field, the model's reading, the new value, the clinician's name, the time, an optional note) and sets `clinician_corrected`. Both set `reviewed_by` and `reviewed_at`. `printed` and `derived` are never changed, and the low-confidence reasons stay, so it is known afterwards what the review answered. Any item can be reviewed, flagged or not; there is no second approver.

**Two document signals must be acknowledged by name.** LC-13 (the document prints another patient) and LC-17 (a results row may be missing) each say something about the whole document that no single value shows, so confirming the document's header values, or correcting one, doesn't clear them on its own. While the document awaits review, a review of it must list each of the two it carries in `acknowledge`, and the page shows each as a statement the clinician ticks ("I have checked that this document belongs to this patient"). The AI service refuses a review that leaves one out or names a signal the document doesn't carry. The acknowledged ids are kept in the review's audit row.

**The reviewed view is computed, not stored** (`scripts/src/record/review.py`, `reviewed_view`): each corrected field replaced by its latest correction, and the item's derived values computed again from it by the same code that built them (a corrected result gets its number and flag again; a corrected report date moves the document date). The patient summary and the document page show this view; the page's list of values reads it from `GET /documents/{id}/record?view=reviewed`, for which the API asks the AI service (`POST /internal/reviewed-view`, a pure function like the one below), so the view is never computed twice in two languages. The summary marks each corrected value "corrected by Dr X on date"; the document page also says what was read. A date added to an undated document is the one correction with no original; it is never written into `printed`, and the view's derived dates include it (ADR 0003).

**A corrected value goes through the checks that apply to a value on its own**: units and ranges, printed flag against computed flag, dates. It is never checked against the transcription (the source-line, text-layer and range-bound checks): the clinician read the original upload, not the transcription. What the checks find is shown to the clinician, who must acknowledge it to save; it never refuses the value, since the document itself may be wrong and is never "corrected" by code (ADR 0004).

**A document is verified** once nothing in it is low confidence. Its page is built again from the reviewed view, and its patient's summary is rebuilt.

**Who does what.** The Go API owns the write: it checks the permission (`review.write`) and the organisation, loads the record, asks the AI service to apply the review (`POST /internal/review-apply`, a pure function: record in, reviewed record, checks and page out; it reads and writes nothing), then stores the new record, a row of `record_reviews` (who, when, which item and field, the signal ids the item carried, whether it was an audit-sample review) and the audit event in one transaction. The record's `updated_at` guards against two clinicians saving over each other (409). A document being processed can't be reviewed.

**The review shows the original upload**, not page images rendered again: `GET /documents/{id}/file` (permission `document.read`, organisation-scoped like every document route, audited as `document_file_viewed` before a byte is sent) serves the stored file with the type its extension had when the upload checked it against the file's first bytes, under a generic file name. Images show directly; a PDF opens in the browser's own viewer at the page being checked (`#page=N`). It is the one API response that may be framed: its content security policy names the app's origins in `frame-ancestors` and drops `X-Frame-Options`, and still allows no script and no load from elsewhere. Every other response, its refusals included, keeps `frame-ancestors 'none'` (ADR 0010).

**Measuring the signals from reviews.** Every review row keeps the signal ids its item carried, so each signal's precision comes from use: of the items a signal raised, the share a clinician corrected (a corrected field of the item is a true catch; an item only confirmed was a false alarm). Each item counts once per run, however many reviews it took. The view `review_signal_precision` holds the counts per organisation and signal, and `python -m evals.review_signals` reports them with Wilson intervals. Precision says nothing about what the signals miss: a verified document is never looked at.

**So a share of verified documents is audited.** When a run ends verified, the AI service picks it for an audit at `MEDSUM_AUDIT_SAMPLE_RATE` (default 0.10), from a hash of the run's id rather than a random draw, so a resumed run picks the same way, and stores an `audit_samples` row in the same transaction as the result. The review queue lists open samples beside the documents that need review, marked "audit"; a clinician checks every value, corrects any that are wrong (those reviews are stored with `audit` set) and finishes the audit, which is refused until every value (the document's details, the patient's and every item) has a review made during the audit, so a finished audit is a full check (`POST /documents/{id}/audit-complete`, audited). A correction during an audit is an error that got through unflagged; the view `audit_sample_outcomes` and the same report give that escape rate. A sample is of one run: a reprocessed document's old sample leaves the queue. The document's record reports its open audit and the values not yet reviewed during it, counted by the same rule that finishing the audit checks, so the document page opens in audit mode from the document's own state, however it was opened, marks each value as still to check or checked, and offers to finish only when none is left.

**A reviewed document can become an eval case.** `python -m evals.export_review <document_id> <out_dir>` writes a copy of the upload, a key built from the reviewed view, and a case file naming the reviewed items, fields and signals (never the values). It is a candidate: someone checks the key by hand before it joins the dataset. It runs only under `MEDSUM_ENV=dev`, where every document is synthetic, since it copies a patient's document out of the database.

**Reprocessing** replaces the record, so its reviews start again; the `record_reviews` rows of the earlier run stay.

**The record's schema version stays 1.** `SourceDocument` gained an optional `corrections` list with a default, which every stored record reads the same without; the version is bumped when a field is removed, renamed, made required or changes meaning.

## Alternatives rejected

- **Overwriting `printed` with the correction.** The record would then claim the model read a value it never read, and nothing would be left to measure the signals with.
- **A separate table of corrections applied when pages are built.** Two places to read a value from; every consumer (summary, document page, tools, exports) would have to join them. Inside the record, one reader (`reviewed_view`) serves all of them.
- **Running every validator on a corrected value.** The transcription checks would flag every correction of a misread value, which is the point of correcting it.
- **Refusing a corrected value that fails a check.** A printed flag that disagrees with the right value is the document's problem; the clinician decides.
- **Page images rendered again from the PDF for the review.** A second copy of what the browser can show from the file itself, and a rendering bug would put a wrong image beside the values.
- **A second approver.** Not how a ward works for this kind of check; the trail says who did it.
- **A random draw for the audit sample.** A run resumed by another worker would draw again; the hash of the run's id gives the same share and the same answer every time.
- **Counting precision per review row.** A field confirmed and then corrected would count once each way; the item's outcome is what the signal predicted.
- **The AI service writing the review.** It would need write access to the audit log, and the audit row would no longer share the write's transaction.

## Consequences

- The summary is only as current as its last rebuild: it is rebuilt when a review leaves a document verified, not after every correction of a document still awaiting review (whose values are still marked awaiting review there).
- Correction text is clinical data inside the record; the audit row holds the item id and field name only (ADR 0008).
- A correction of a test's name can move a remark off that test; the reviewed view links remarks again.
- An audit costs a clinician a full check of a document that needed none; the rate is the dial between that time and how tightly the escape rate is known. At 10% of a few hundred verified documents, the interval stays wide for a long time.
- The AI service's role can add audit samples and read the two views, never `record_reviews` itself, which holds corrected values.

## What would change it

Real use showing clinicians want to correct structure (split one row into two, add a value the model dropped) rather than fields: that needs adding items, which this design doesn't do. A regulator requiring a second signature on corrections.
