package db

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

// createSession stores a session directly; the API does it in RecordLogin.
func createSession(s *Store, sess Session) error {
	ctx := context.Background()
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error { return insertSession(ctx, tx, sess) })
}

// during runs other while an open transaction holds what hold did, commits
// once n of other's calls wait for it, and waits for them: the order two
// requests at once can take, made certain.
func during(t *testing.T, s *Store, hold func(tx pgx.Tx) error, n int, other func(i int)) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = tx.Rollback(context.Background()) }()
	if err := hold(tx); err != nil {
		t.Fatal(err)
	}
	done := make(chan struct{}, n)
	for i := range n {
		go func() {
			other(i)
			done <- struct{}{}
		}()
	}
	for waiting := 0; waiting < n; {
		if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM pg_stat_activity
WHERE datname = current_database() AND wait_event_type = 'Lock'`).Scan(&waiting); err != nil {
			t.Fatalf("the other calls never waited: %v", err)
		}
		time.Sleep(5 * time.Millisecond)
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	for range n {
		<-done
	}
}

func sessionFor(userID, token string) Session {
	return Session{TokenHash: hashOf(token), UserID: userID, CreatedAt: t0, LastSeenAt: t0,
		ExpiresAt: t0.Add(time.Hour)}
}

func TestALoginCheckedBeforeAResetMakesNoSession(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	u := mustUser(t, s, "asha@hospital.test")
	activate(t, s, u.ID)
	got, _ := s.GetUserByID(ctx, u.ID)
	rehash := "rehash of the old password"

	var err error
	during(t, s, func(tx pgx.Tx) error { // the reset, as CompletePasswordSetup does it
		_, err := tx.Exec(ctx, `UPDATE staff_users SET password_hash = 'new' WHERE id = $1`, u.ID)
		return err
	}, 1, func(int) {
		err = s.RecordLogin(ctx, Login{UserID: u.ID, VerifiedHash: *got.PasswordHash, NewHash: &rehash,
			Session: sessionFor(u.ID, "a"), At: t0}, nil)
	})

	if !errors.Is(err, ErrCredentialsChanged) {
		t.Errorf("error = %v, want ErrCredentialsChanged", err)
	}
	if sessions, _ := s.ListUserSessions(ctx, u.ID); len(sessions) != 0 {
		t.Error("the old password got a session")
	}
	if after, _ := s.GetUserByID(ctx, u.ID); *after.PasswordHash != "new" {
		t.Errorf("hash = %q: the rehash replaced the new password", *after.PasswordHash)
	}
}

func TestRecordLoginNeedsAnActiveAccount(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	u := mustUser(t, s, "asha@hospital.test")
	activate(t, s, u.ID)
	got, _ := s.GetUserByID(ctx, u.ID)
	if err := s.SetUserStatus(ctx, u.ID, StatusDisabled, nil); err != nil {
		t.Fatal(err)
	}
	err := s.RecordLogin(ctx, Login{UserID: u.ID, VerifiedHash: *got.PasswordHash, Session: sessionFor(u.ID, "a"), At: t0}, nil)
	if !errors.Is(err, ErrCredentialsChanged) {
		t.Errorf("error = %v, want ErrCredentialsChanged", err)
	}
}

func TestChangePasswordEndsTheOtherSessionsOrChangesNothing(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	u := mustUser(t, s, "asha@hospital.test")
	activate(t, s, u.ID)
	got, _ := s.GetUserByID(ctx, u.ID)
	for _, token := range []string{"mine", "theirs"} {
		if err := createSession(s, sessionFor(u.ID, token)); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.ChangePassword(ctx, u.ID, "stale", "new", hashOf("mine"), t0, nil); !errors.Is(err, ErrCredentialsChanged) {
		t.Errorf("stale hash: %v, want ErrCredentialsChanged", err)
	}
	// An event the database refuses fails the last step: all of it rolls back.
	bad := &AuditEvent{Action: "NOT VALID", Outcome: OutcomeSuccess}
	if err := s.ChangePassword(ctx, u.ID, *got.PasswordHash, "new", hashOf("mine"), t0, bad); err == nil {
		t.Error("the refused event was accepted")
	}
	sessions, _ := s.ListUserSessions(ctx, u.ID)
	if after, _ := s.GetUserByID(ctx, u.ID); *after.PasswordHash != *got.PasswordHash || len(sessions) != 2 {
		t.Errorf("a failed change left hash %q and %d sessions", *after.PasswordHash, len(sessions))
	}

	if err := s.ChangePassword(ctx, u.ID, *got.PasswordHash, "new", hashOf("mine"), t0, nil); err != nil {
		t.Fatal(err)
	}
	sessions, _ = s.ListUserSessions(ctx, u.ID)
	if len(sessions) != 1 || string(sessions[0].TokenHash) != string(hashOf("mine")) {
		t.Errorf("sessions = %+v, want only the caller's", sessions)
	}
}

func TestTwoLinksAtOnceLeaveOneUsable(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	u := mustUser(t, s, "asha@hospital.test")
	tokens := []string{"one", "two"}
	errs := make([]error, 2)

	during(t, s, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `SELECT 1 FROM staff_users WHERE id = $1 FOR UPDATE`, u.ID)
		return err
	}, 2, func(i int) {
		errs[i] = s.CreateOneTimeToken(ctx, OneTimeToken{TokenHash: hashOf(tokens[i]), UserID: u.ID,
			Purpose: PurposeSetup, CreatedAt: t0, ExpiresAt: t0.Add(time.Hour)}, nil)
	})

	usable := 0
	for i, token := range tokens {
		if errs[i] != nil {
			t.Fatal(errs[i])
		}
		if got, _ := s.GetUsableToken(ctx, hashOf(token), t0); got != nil {
			usable++
		}
	}
	if usable != 1 {
		t.Errorf("%d usable links, want 1", usable)
	}
}

func TestANameEditDoesNotUndoASetup(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	u := mustUser(t, s, "asha@hospital.test")
	name := "Dr Asha Rao"

	var err error
	during(t, s, func(tx pgx.Tx) error { // the setup, as CompletePasswordSetup does it
		_, err := tx.Exec(ctx, `UPDATE staff_users SET password_hash = 'h', status = 'active' WHERE id = $1`, u.ID)
		return err
	}, 1, func(int) {
		_, err = s.UpdateUser(ctx, UserUpdate{OrgID: u.OrgID, UserID: u.ID, FullName: &name}, nil)
	})

	if err != nil {
		t.Fatal(err)
	}
	if got, _ := s.GetUserByID(ctx, u.ID); got.Status != StatusActive || got.FullName != name {
		t.Errorf("status %q, name %q; want active and the new name", got.Status, got.FullName)
	}
}

// A reprocess that began before the audit was finished: the audit of the old
// run's record must not verify what the new run leaves.
func TestAnAuditIsNotFinishedOnceAReprocessBegan(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	at := time.Date(2026, 11, 10, 9, 0, 0, 0, time.UTC)
	me, d := reviewFixture(t, s, "checks_passed", at)
	run := mustRun(t, s, d.ID)
	for _, q := range []string{
		`UPDATE document_records SET run_id = $2 WHERE document_id = $1`,
		`INSERT INTO audit_samples (document_id, run_id, sampled_at) VALUES ($1, $2, '2026-11-10')`,
		`INSERT INTO record_reviews (document_id, run_id, item_id, action, audit, reviewed_at)
SELECT $1, $2, i, 'confirm', true, '2026-11-11' FROM unnest('{document,patient,obs-1,obs-2,med-1}'::text[]) i`,
	} {
		if _, err := s.pool.Exec(ctx, q, d.ID, run); err != nil {
			t.Fatal(err)
		}
	}

	var err error
	finish := func(int) {
		err = s.CompleteAudit(ctx, me.OrgID, d.ID, me.ID, at, ev(me.OrgID, me.ID, "audit_completed"))
	}
	during(t, s, func(tx pgx.Tx) error { // as document_jobs.enqueue does it
		_, err := tx.Exec(ctx, `UPDATE insights SET status = 'processing' WHERE document_id = $1`, d.ID)
		return err
	}, 1, finish)
	if !errors.Is(err, ErrNoOpenAudit) {
		t.Errorf("as the reprocess began: err = %v, want ErrNoOpenAudit", err)
	}
	finish(0) // and while it runs, the old record still in place
	if !errors.Is(err, ErrNoOpenAudit) {
		t.Errorf("during the reprocess: err = %v, want ErrNoOpenAudit", err)
	}
	var open bool
	if err := s.pool.QueryRow(ctx, `SELECT completed_at IS NULL FROM audit_samples WHERE run_id = $1`, run).Scan(&open); err != nil || !open {
		t.Errorf("the sample was finished: open %v (%v)", open, err)
	}

	// The same audit can be finished while nothing is being processed.
	if _, err := s.pool.Exec(ctx, `UPDATE insights SET status = 'checks_passed' WHERE document_id = $1`, d.ID); err != nil {
		t.Fatal(err)
	}
	if finish(0); err != nil {
		t.Errorf("with no reprocess: %v", err)
	}
}
