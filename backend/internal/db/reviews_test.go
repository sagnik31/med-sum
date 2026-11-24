package db

import (
	"context"
	"errors"
	"testing"
	"time"
)

// A record with the document, one item of two groups and the patient
// awaiting review, and one item checked.
const flaggedRecord = `{"schema_version": 1,
  "document": {"provenance": {"status": "low_confidence"}, "type": "lab_report",
               "derived": {"document_date": "2025-03-03"}},
  "patient": {"provenance": {"status": "low_confidence"}},
  "pages": [{"page": 1, "lines": ["low_confidence"]}],
  "observations": [{"id": "obs-1", "provenance": {"status": "low_confidence"}},
                   {"id": "obs-2", "provenance": {"status": "validated"}}],
  "medications": [{"id": "med-1", "provenance": {"status": "low_confidence"}}]}`

func reviewFixture(t *testing.T, s *Store, status string, at time.Time) (*User, *Document) {
	t.Helper()
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	d := mustDocument(t, s, mustPatient(t, s, me.OrgID, me.ID, "SH-1"), me, "a.pdf")
	if _, err := s.pool.Exec(ctx, `INSERT INTO insights (document_id, status, updated_at) VALUES ($1, $2, $3)`,
		d.ID, status, at); err != nil {
		t.Fatal(err)
	}
	if _, err := s.pool.Exec(ctx, `INSERT INTO document_records (document_id, schema_version, record, updated_at)
VALUES ($1, 1, $2, $3)`, d.ID, flaggedRecord, at); err != nil {
		t.Fatal(err)
	}
	return me, d
}

func TestTheReviewQueueCountsFlaggedValuesAndStaysInTheOrganisation(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	at := time.Date(2026, 11, 10, 9, 0, 0, 0, time.UTC)
	me, d := reviewFixture(t, s, "needs_review", at)

	// An older one of the same organisation, a verified one, and another
	// organisation's.
	p, _ := s.GetPatient(ctx, me.OrgID, d.PatientID)
	older := mustDocument(t, s, p, me, "b.pdf")
	setInsight(t, s, older.ID, "needs_review", nil)
	if _, err := s.pool.Exec(ctx, `UPDATE insights SET updated_at = $2 WHERE document_id = $1`, older.ID, at.Add(-time.Hour)); err != nil {
		t.Fatal(err)
	}
	done := mustDocument(t, s, p, me, "c.pdf")
	setInsight(t, s, done.ID, "verified", nil)
	otherOrg, them := mustOtherOrg(t, s)
	theirs := mustDocument(t, s, mustPatient(t, s, otherOrg, them.ID, "OT-1"), them, "d.pdf")
	setInsight(t, s, theirs.ID, "needs_review", nil)

	queue, err := s.ListReviewQueue(ctx, me.OrgID)
	if err != nil {
		t.Fatal(err)
	}
	if len(queue) != 2 || queue[0].DocumentID != older.ID || queue[1].DocumentID != d.ID {
		t.Fatalf("queue = %+v", queue)
	}
	e := queue[1]
	// document, patient, obs-1 and med-1; a page line saying the same
	// words doesn't count.
	if e.Flagged != 4 || e.Reason != "needs_review" || e.PatientMRN != "SH-1" || e.DocumentType == nil ||
		*e.DocumentType != "lab_report" || *e.DocumentDate != "2025-03-03" || !e.QueuedAt.Equal(at) {
		t.Errorf("entry = %+v", e)
	}
	if queue[0].Flagged != 0 {
		t.Errorf("a document without a record has %d flagged", queue[0].Flagged)
	}

	if _, err := s.SoftDeleteDocument(ctx, me.OrgID, older.ID, me.ID, at, nil); err != nil {
		t.Fatal(err)
	}
	if queue, _ = s.ListReviewQueue(ctx, me.OrgID); len(queue) != 1 {
		t.Errorf("a deleted document is still queued: %+v", queue)
	}
}

func TestApplyReviewStoresTheRecordTheRowAndTheAuditEventTogether(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	at := time.Date(2026, 11, 10, 9, 0, 0, 0, time.UTC).Truncate(time.Microsecond)
	me, d := reviewFixture(t, s, "needs_review", at)
	field, original, corrected := "value_text", "10.4", "11.4"

	reviewed := at.Add(time.Minute)
	updated, err := s.ApplyReview(ctx, me.OrgID, d.ID, at, RecordReview{
		Record: []byte(`{"schema_version": 1, "reviewed": true}`), HTML: "<p>new</p>",
		ItemID: "obs-1", Field: &field, Action: "correct", Original: &original, Corrected: &corrected,
		Signals: []string{"LC-03"}, Findings: []string{"flag_agreement"}, ReviewerID: me.ID, At: reviewed,
	}, ev(me.OrgID, me.ID, "field_corrected"))
	if err != nil {
		t.Fatal(err)
	}
	if !updated.Equal(reviewed) {
		t.Errorf("updated_at = %v", updated)
	}
	rec, _ := s.GetDocumentRecord(ctx, d.ID)
	ins, _ := s.GetInsightByDocumentID(ctx, d.ID)
	if string(rec.Record) != `{"reviewed": true, "schema_version": 1}` || ins.Status != "needs_review" ||
		ins.HTMLInsights != "<p>new</p>" {
		t.Errorf("record = %s, insight = %+v", rec.Record, ins)
	}
	var row struct {
		item, action, orig, corr string
		signals, findings        []string
		reviewer                 string
	}
	if err := s.pool.QueryRow(ctx, `SELECT item_id, action, original, corrected, signals, findings, reviewer_id::text
FROM record_reviews WHERE document_id = $1`, d.ID).Scan(&row.item, &row.action, &row.orig, &row.corr,
		&row.signals, &row.findings, &row.reviewer); err != nil {
		t.Fatal(err)
	}
	if row.item != "obs-1" || row.action != "correct" || row.orig != "10.4" || row.corr != "11.4" ||
		len(row.signals) != 1 || row.signals[0] != "LC-03" || row.findings[0] != "flag_agreement" || row.reviewer != me.ID {
		t.Errorf("row = %+v", row)
	}
	events, _ := s.ListAuditEvents(ctx, me.OrgID, AuditFilter{})
	if len(events) != 1 || events[0].Action != "field_corrected" {
		t.Errorf("events = %+v", events)
	}

	// The same updated_at again: the record changed meanwhile.
	_, err = s.ApplyReview(ctx, me.OrgID, d.ID, at, RecordReview{Record: []byte(`{}`), ItemID: "obs-1",
		Action: "confirm", ReviewerID: me.ID, At: reviewed}, ev(me.OrgID, me.ID, "field_confirmed"))
	if !errors.Is(err, ErrReviewConflict) {
		t.Errorf("stale review: err = %v", err)
	}
	var reviews, audits int
	_ = s.pool.QueryRow(ctx, `SELECT count(*) FROM record_reviews`).Scan(&reviews)
	_ = s.pool.QueryRow(ctx, `SELECT count(*) FROM audit_events`).Scan(&audits)
	if reviews != 1 || audits != 1 {
		t.Errorf("a refused review wrote %d reviews, %d audit rows", reviews, audits)
	}
}

func TestApplyReviewRefusesWhileProcessingAndAcrossOrganisations(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	at := time.Date(2026, 11, 10, 9, 0, 0, 0, time.UTC)
	me, d := reviewFixture(t, s, "processing", at)
	rv := RecordReview{Record: []byte(`{}`), ItemID: "obs-1", Action: "confirm", ReviewerID: me.ID, At: at}
	if _, err := s.ApplyReview(ctx, me.OrgID, d.ID, at, rv, nil); !errors.Is(err, ErrReviewConflict) {
		t.Errorf("while processing: err = %v", err)
	}
	if _, err := s.pool.Exec(ctx, `UPDATE insights SET status = 'needs_review' WHERE document_id = $1`, d.ID); err != nil {
		t.Fatal(err)
	}
	otherOrg, _ := mustOtherOrg(t, s)
	if _, err := s.ApplyReview(ctx, otherOrg, d.ID, at, rv, nil); !errors.Is(err, ErrReviewConflict) {
		t.Errorf("another organisation: err = %v", err)
	}
	if _, err := s.ApplyReview(ctx, me.OrgID, d.ID, at, rv, nil); err != nil {
		t.Errorf("own organisation: %v", err)
	}
}

// A review never verifies: a needs_review document is signed off once
// nothing in it is flagged, and stays in the queue until then.
func TestAReviewedDocumentIsQueuedUntilSignedOff(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	at := time.Date(2026, 11, 10, 9, 0, 0, 0, time.UTC)
	me, d := reviewFixture(t, s, "needs_review", at)
	signOff := func(read time.Time) error {
		return s.SignOff(ctx, me.OrgID, d.ID, read, at.Add(time.Hour), ev(me.OrgID, me.ID, "document_signed_off"))
	}
	if err := signOff(at); !errors.Is(err, ErrFlaggedBeforeSignOff) {
		t.Fatalf("with values flagged: err = %v", err)
	}
	reviewed := at.Add(time.Minute)
	if _, err := s.ApplyReview(ctx, me.OrgID, d.ID, at, RecordReview{Record: []byte(`{"schema_version": 1}`),
		HTML: "<p></p>", ItemID: "obs-1", Action: "confirm", ReviewerID: me.ID, At: reviewed},
		ev(me.OrgID, me.ID, "field_confirmed")); err != nil {
		t.Fatal(err)
	}
	queue, _ := s.ListReviewQueue(ctx, me.OrgID)
	if len(queue) != 1 || queue[0].Reason != "needs_review" || queue[0].Flagged != 0 {
		t.Fatalf("queue = %+v", queue)
	}
	if err := signOff(at); !errors.Is(err, ErrReviewConflict) {
		t.Errorf("a stale sign-off: err = %v", err)
	}
	if err := signOff(reviewed); err != nil {
		t.Fatal(err)
	}
	if ins, _ := s.GetInsightByDocumentID(ctx, d.ID); ins.Status != "verified" {
		t.Errorf("status = %s", ins.Status)
	}
}

// No run ends verified: a document that passed every check is queued as
// checks_passed and becomes verified only when a clinician signs it off.
func TestADocumentThatPassedItsChecksIsQueuedUntilSignedOff(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	at := time.Date(2026, 11, 10, 9, 0, 0, 0, time.UTC)
	me, d := reviewFixture(t, s, "checks_passed", at)

	queue, err := s.ListReviewQueue(ctx, me.OrgID)
	if err != nil || len(queue) != 1 || queue[0].Reason != "checks_passed" || queue[0].Flagged != 4 {
		t.Fatalf("queue = %+v (%v)", queue, err)
	}
	// A review with nothing left flagged doesn't verify it.
	if _, err := s.ApplyReview(ctx, me.OrgID, d.ID, at, RecordReview{Record: []byte(`{"schema_version": 1}`),
		HTML: "<p></p>", ItemID: "obs-1", Action: "confirm", ReviewerID: me.ID,
		At: at.Add(time.Minute)}, ev(me.OrgID, me.ID, "field_confirmed")); err != nil {
		t.Fatal(err)
	}
	if ins, _ := s.GetInsightByDocumentID(ctx, d.ID); ins.Status != "checks_passed" {
		t.Fatalf("status after a review = %s", ins.Status)
	}

	otherOrg, them := mustOtherOrg(t, s)
	if err := s.SignOff(ctx, otherOrg, d.ID, at, at, ev(otherOrg, them.ID, "document_signed_off")); !errors.Is(err, ErrNotAwaitingSignOff) {
		t.Errorf("another organisation: err = %v", err)
	}
	done := at.Add(time.Hour)
	// A clinician who read the record before that review, or before a
	// reprocess, has not read what they would sign off.
	if err := s.SignOff(ctx, me.OrgID, d.ID, at, done, ev(me.OrgID, me.ID, "document_signed_off")); !errors.Is(err, ErrReviewConflict) {
		t.Errorf("a stale sign-off: err = %v", err)
	}
	if ins, _ := s.GetInsightByDocumentID(ctx, d.ID); ins.Status != "checks_passed" {
		t.Fatalf("status after a stale sign-off = %s", ins.Status)
	}
	if err := s.SignOff(ctx, me.OrgID, d.ID, at.Add(time.Minute), done, ev(me.OrgID, me.ID, "document_signed_off")); err != nil {
		t.Fatal(err)
	}
	ins, _ := s.GetInsightByDocumentID(ctx, d.ID)
	if ins.Status != "verified" || ins.UpdatedAt == nil || !ins.UpdatedAt.Equal(done) {
		t.Errorf("insight = %+v", ins)
	}
	if queue, _ = s.ListReviewQueue(ctx, me.OrgID); len(queue) != 0 {
		t.Errorf("a signed-off document is still queued: %+v", queue)
	}
	if err := s.SignOff(ctx, me.OrgID, d.ID, at.Add(time.Minute), done, ev(me.OrgID, me.ID, "document_signed_off")); !errors.Is(err, ErrNotAwaitingSignOff) {
		t.Errorf("signed off twice: err = %v", err)
	}
	var n int
	_ = s.pool.QueryRow(ctx, `SELECT count(*) FROM audit_events WHERE action = 'document_signed_off'`).Scan(&n)
	if n != 1 {
		t.Errorf("%d document_signed_off events, want 1", n)
	}
}
