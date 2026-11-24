package db

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func mustRun(t *testing.T, s *Store, docID string) string {
	t.Helper()
	var id string
	if err := s.pool.QueryRow(context.Background(), `
INSERT INTO pipeline_runs (document_id, graph_version, ended_at, end_state)
VALUES ($1, 'test', now(), 'checks_passed') RETURNING id::text`, docID).Scan(&id); err != nil {
		t.Fatal(err)
	}
	return id
}

func TestAnAuditSampleIsQueuedReviewedAsAnAuditAndFinished(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	at := time.Date(2026, 11, 10, 9, 0, 0, 0, time.UTC).Truncate(time.Microsecond)
	me, d := reviewFixture(t, s, "checks_passed", at)
	run := mustRun(t, s, d.ID)
	if _, err := s.pool.Exec(ctx, `UPDATE document_records SET run_id = $2 WHERE document_id = $1`, d.ID, run); err != nil {
		t.Fatal(err)
	}
	sampled := at.Add(time.Minute)
	if _, err := s.pool.Exec(ctx, `INSERT INTO audit_samples (run_id, document_id, sampled_at) VALUES ($1, $2, $3)`,
		run, d.ID, sampled); err != nil {
		t.Fatal(err)
	}

	queue, err := s.ListReviewQueue(ctx, me.OrgID)
	if err != nil {
		t.Fatal(err)
	}
	if len(queue) != 1 || queue[0].Reason != "audit" || !queue[0].QueuedAt.Equal(at) {
		t.Fatalf("queue = %+v", queue)
	}

	// A correction during the audit: an error the pipeline let through.
	field, orig, corr := "value_text", "10.4", "11.4"
	if _, err := s.ApplyReview(ctx, me.OrgID, d.ID, at, RecordReview{Record: []byte(flaggedRecord),
		HTML: "<p></p>", RunID: &run, ItemID: "obs-2", Field: &field, Action: "correct",
		Original: &orig, Corrected: &corr, ReviewerID: me.ID, At: sampled.Add(time.Minute)},
		ev(me.OrgID, me.ID, "field_corrected")); err != nil {
		t.Fatal(err)
	}
	var audit bool
	if err := s.pool.QueryRow(ctx, `SELECT audit FROM record_reviews WHERE document_id = $1`, d.ID).Scan(&audit); err != nil || !audit {
		t.Errorf("the review during an audit has audit = %v (%v)", audit, err)
	}
	// A review leaves it checks_passed, and it can't be signed off while
	// its audit is open.
	if ins, _ := s.GetInsightByDocumentID(ctx, d.ID); ins == nil || ins.Status != "checks_passed" {
		t.Errorf("insight after a review = %+v", ins)
	}
	if err := s.SignOff(ctx, me.OrgID, d.ID, sampled, sampled, ev(me.OrgID, me.ID, "document_signed_off")); !errors.Is(err, ErrAuditBeforeSignOff) {
		t.Errorf("a sign-off during the audit: err = %v", err)
	}

	// Another organisation can't finish it.
	otherOrg, them := mustOtherOrg(t, s)
	if err := s.CompleteAudit(ctx, otherOrg, d.ID, them.ID, at, ev(otherOrg, them.ID, "audit_completed")); !errors.Is(err, ErrNoOpenAudit) {
		t.Errorf("another organisation: err = %v", err)
	}
	done := sampled.Add(time.Hour)

	// Not before every value is confirmed or corrected: the document, the
	// patient and three items, one of them corrected so far. A review from
	// before the sample doesn't count.
	if _, err := s.pool.Exec(ctx, `
INSERT INTO record_reviews (document_id, run_id, item_id, action, audit, reviewer_id, reviewed_at)
VALUES ($1, $2, 'obs-1', 'confirm', true, $3, $4)`, d.ID, run, me.ID, at); err != nil {
		t.Fatal(err)
	}
	var incomplete *AuditIncompleteError
	err = s.CompleteAudit(ctx, me.OrgID, d.ID, me.ID, done, ev(me.OrgID, me.ID, "audit_completed"))
	if !errors.As(err, &incomplete) || incomplete.Unreviewed != 4 {
		t.Fatalf("an incomplete audit: err = %v", err)
	}
	// The open audit names the same values, for the review screen to mark.
	open, err := s.GetOpenAudit(ctx, d.ID)
	if err != nil || open == nil || !open.SampledAt.Equal(sampled) ||
		strings.Join(open.Unreviewed, ",") != "document,med-1,obs-1,patient" {
		t.Fatalf("open audit = %+v (%v)", open, err)
	}
	for _, item := range []string{"document", "patient", "obs-1", "med-1"} {
		if _, err := s.pool.Exec(ctx, `
INSERT INTO record_reviews (document_id, run_id, item_id, action, audit, reviewer_id, reviewed_at)
VALUES ($1, $2, $3, 'confirm', true, $4, $5)`, d.ID, run, item, me.ID, sampled.Add(2*time.Minute)); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.CompleteAudit(ctx, me.OrgID, d.ID, me.ID, done, ev(me.OrgID, me.ID, "audit_completed")); err != nil {
		t.Fatal(err)
	}
	if err := s.CompleteAudit(ctx, me.OrgID, d.ID, me.ID, done, ev(me.OrgID, me.ID, "audit_completed")); !errors.Is(err, ErrNoOpenAudit) {
		t.Errorf("finished twice: err = %v", err)
	}
	if open, err := s.GetOpenAudit(ctx, d.ID); err != nil || open != nil {
		t.Errorf("a finished audit is still open: %+v (%v)", open, err)
	}
	if queue, _ = s.ListReviewQueue(ctx, me.OrgID); len(queue) != 0 {
		t.Errorf("a finished audit is still queued: %+v", queue)
	}
	if ins, _ := s.GetInsightByDocumentID(ctx, d.ID); ins == nil || ins.Status != "verified" {
		t.Errorf("a finished audit did not verify the document: %+v", ins)
	}
	var finished, escaped bool
	if err := s.pool.QueryRow(ctx, `SELECT finished, escaped FROM audit_sample_outcomes WHERE run_id = $1`, run).
		Scan(&finished, &escaped); err != nil || !finished || !escaped {
		t.Errorf("outcome: finished %v, escaped %v (%v)", finished, escaped, err)
	}
	var audits int
	_ = s.pool.QueryRow(ctx, `SELECT count(*) FROM audit_events WHERE action = 'audit_completed'`).Scan(&audits)
	if audits != 1 {
		t.Errorf("%d audit_completed events, want 1", audits)
	}
}

// A reprocessed document's old sample stays open but is dead: it isn't
// queued, reviews of the new record aren't audit reviews, it can't be
// finished, and a correction of the new record isn't counted as its escape.
func TestAnAuditSampleOfAnOlderRunIsDead(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	at := time.Date(2026, 11, 10, 9, 0, 0, 0, time.UTC).Truncate(time.Microsecond)
	me, d := reviewFixture(t, s, "needs_review", at)
	old, current := mustRun(t, s, d.ID), mustRun(t, s, d.ID)
	if _, err := s.pool.Exec(ctx, `UPDATE document_records SET run_id = $2 WHERE document_id = $1`, d.ID, current); err != nil {
		t.Fatal(err)
	}
	sampled := at.Add(-time.Hour) // the old run was sampled before the reprocess
	if _, err := s.pool.Exec(ctx, `INSERT INTO audit_samples (run_id, document_id, sampled_at) VALUES ($1, $2, $3)`,
		old, d.ID, sampled); err != nil {
		t.Fatal(err)
	}
	if queue, _ := s.ListReviewQueue(ctx, me.OrgID); len(queue) != 1 || queue[0].Reason != "needs_review" {
		t.Errorf("a reprocessed document's old sample is queued: %+v", queue)
	}
	if open, err := s.GetOpenAudit(ctx, d.ID); err != nil || open != nil {
		t.Errorf("a reprocessed document's old sample is open: %+v (%v)", open, err)
	}

	// The new run's flagged value is corrected: a caught error, not an escape.
	field, orig, corr := "value_text", "10.4", "11.4"
	if _, err := s.ApplyReview(ctx, me.OrgID, d.ID, at, RecordReview{Record: []byte(flaggedRecord),
		HTML: "<p></p>", RunID: &current, ItemID: "obs-2", Field: &field, Action: "correct",
		Original: &orig, Corrected: &corr, Signals: []string{"LC-03"}, ReviewerID: me.ID, At: at.Add(time.Minute)},
		ev(me.OrgID, me.ID, "field_corrected")); err != nil {
		t.Fatal(err)
	}
	var audit bool
	if err := s.pool.QueryRow(ctx, `SELECT audit FROM record_reviews WHERE document_id = $1`, d.ID).Scan(&audit); err != nil || audit {
		t.Errorf("a review of the new record has audit = %v (%v)", audit, err)
	}
	var reviewed int
	_ = s.pool.QueryRow(ctx, `SELECT reviewed FROM review_signal_precision WHERE org_id = $1 AND signal = 'LC-03'`,
		me.OrgID).Scan(&reviewed)
	if reviewed != 1 {
		t.Errorf("signal precision counts %d reviews of LC-03, want 1", reviewed)
	}
	var escaped bool
	if err := s.pool.QueryRow(ctx, `SELECT escaped FROM audit_sample_outcomes WHERE run_id = $1`, old).
		Scan(&escaped); err != nil || escaped {
		t.Errorf("the old sample: escaped %v (%v)", escaped, err)
	}

	if err := s.CompleteAudit(ctx, me.OrgID, d.ID, me.ID, at.Add(time.Hour), ev(me.OrgID, me.ID, "audit_completed")); !errors.Is(err, ErrNoOpenAudit) {
		t.Errorf("finishing the old sample: err = %v", err)
	}
	var open bool
	if err := s.pool.QueryRow(ctx, `SELECT completed_at IS NULL FROM audit_samples WHERE run_id = $1`, old).
		Scan(&open); err != nil || !open {
		t.Errorf("the old sample was written to: open %v (%v)", open, err)
	}
}

// Precision counts each item once per run: corrected if any review of it
// corrected a field, else confirmed. Audit reviews stay out.
func TestSignalPrecisionCountsEachReviewedItemOnce(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me, d := reviewFixture(t, s, "needs_review", time.Now())
	run := mustRun(t, s, d.ID)
	for _, r := range []struct {
		item, action string
		signals      []string
		audit        bool
	}{
		{"obs-1", "confirm", []string{"LC-03"}, false},
		{"obs-1", "correct", []string{"LC-03"}, false}, // the same item, then corrected
		{"med-1", "confirm", []string{"LC-03", "LC-05"}, false},
		{"document", "confirm", []string{"LC-12"}, false},
		{"obs-2", "correct", []string{"LC-12"}, true}, // an audit review
	} {
		var field, corrected *string
		if r.action == "correct" {
			f, c := "value_text", "1"
			field, corrected = &f, &c
		}
		if _, err := s.pool.Exec(ctx, `
INSERT INTO record_reviews (document_id, run_id, item_id, field, action, corrected, signals, audit, reviewer_id)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)`, d.ID, run, r.item, field, r.action, corrected, r.signals, r.audit, me.ID); err != nil {
			t.Fatal(err)
		}
	}
	rows, err := s.pool.Query(ctx, `SELECT signal, reviewed, corrected, confirmed FROM review_signal_precision
WHERE org_id = $1 ORDER BY signal`, me.OrgID)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	got := map[string][3]int{}
	for rows.Next() {
		var sig string
		var n [3]int
		if err := rows.Scan(&sig, &n[0], &n[1], &n[2]); err != nil {
			t.Fatal(err)
		}
		got[sig] = n
	}
	want := map[string][3]int{"LC-03": {2, 1, 1}, "LC-05": {1, 0, 1}, "LC-12": {1, 0, 1}}
	if len(got) != len(want) {
		t.Fatalf("precision = %v", got)
	}
	for sig, n := range want {
		if got[sig] != n {
			t.Errorf("%s = %v, want %v", sig, got[sig], n)
		}
	}
}
