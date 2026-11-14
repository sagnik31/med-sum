package db

import (
	"context"
	"errors"
	"testing"
	"time"
)

func mustRun(t *testing.T, s *Store, docID string) string {
	t.Helper()
	var id string
	if err := s.pool.QueryRow(context.Background(), `
INSERT INTO pipeline_runs (document_id, graph_version, ended_at, end_state)
VALUES ($1, 'test', now(), 'verified') RETURNING id::text`, docID).Scan(&id); err != nil {
		t.Fatal(err)
	}
	return id
}

func TestAnAuditSampleIsQueuedReviewedAsAnAuditAndFinished(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	at := time.Date(2026, 11, 10, 9, 0, 0, 0, time.UTC).Truncate(time.Microsecond)
	me, d := reviewFixture(t, s, "verified", at)
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
	if len(queue) != 1 || queue[0].Reason != "audit" || !queue[0].QueuedAt.Equal(sampled) {
		t.Fatalf("queue = %+v", queue)
	}

	// A correction during the audit: an error the pipeline let through.
	field, orig, corr := "value_text", "10.4", "11.4"
	if _, err := s.ApplyReview(ctx, me.OrgID, d.ID, at, RecordReview{Record: []byte(`{"schema_version": 1}`),
		HTML: "<p></p>", Verified: true, RunID: &run, ItemID: "obs-2", Field: &field, Action: "correct",
		Original: &orig, Corrected: &corr, ReviewerID: me.ID, At: sampled.Add(time.Minute)},
		ev(me.OrgID, me.ID, "field_corrected")); err != nil {
		t.Fatal(err)
	}
	var audit bool
	if err := s.pool.QueryRow(ctx, `SELECT audit FROM record_reviews WHERE document_id = $1`, d.ID).Scan(&audit); err != nil || !audit {
		t.Errorf("the review during an audit has audit = %v (%v)", audit, err)
	}

	// Another organisation can't finish it.
	otherOrg, them := mustOtherOrg(t, s)
	if err := s.CompleteAudit(ctx, otherOrg, d.ID, them.ID, at, ev(otherOrg, them.ID, "audit_completed")); !errors.Is(err, ErrNoOpenAudit) {
		t.Errorf("another organisation: err = %v", err)
	}
	done := sampled.Add(time.Hour)
	if err := s.CompleteAudit(ctx, me.OrgID, d.ID, me.ID, done, ev(me.OrgID, me.ID, "audit_completed")); err != nil {
		t.Fatal(err)
	}
	if err := s.CompleteAudit(ctx, me.OrgID, d.ID, me.ID, done, ev(me.OrgID, me.ID, "audit_completed")); !errors.Is(err, ErrNoOpenAudit) {
		t.Errorf("finished twice: err = %v", err)
	}
	if queue, _ = s.ListReviewQueue(ctx, me.OrgID); len(queue) != 0 {
		t.Errorf("a finished audit is still queued: %+v", queue)
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

func TestAnAuditSampleOfAnOlderRunIsNotQueued(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	at := time.Date(2026, 11, 10, 9, 0, 0, 0, time.UTC)
	me, d := reviewFixture(t, s, "verified", at)
	old, current := mustRun(t, s, d.ID), mustRun(t, s, d.ID)
	if _, err := s.pool.Exec(ctx, `UPDATE document_records SET run_id = $2 WHERE document_id = $1`, d.ID, current); err != nil {
		t.Fatal(err)
	}
	if _, err := s.pool.Exec(ctx, `INSERT INTO audit_samples (run_id, document_id) VALUES ($1, $2)`, old, d.ID); err != nil {
		t.Fatal(err)
	}
	if queue, _ := s.ListReviewQueue(ctx, me.OrgID); len(queue) != 0 {
		t.Errorf("a reprocessed document's old sample is queued: %+v", queue)
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
