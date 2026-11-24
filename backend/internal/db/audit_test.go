package db

import (
	"context"
	"strings"
	"testing"
	"time"
)

// event is an audit event in the organisation, by the account.
func event(orgID, actorID, action string) *AuditEvent {
	return &AuditEvent{OrgID: &orgID, ActorID: &actorID, Action: action, Outcome: OutcomeSuccess,
		IP: "192.0.2.7", UserAgent: "test"}
}

func auditRows(t *testing.T, s *Store, orgID string) []AuditEvent {
	t.Helper()
	events, err := s.ListAuditEvents(context.Background(), orgID, AuditFilter{})
	if err != nil {
		t.Fatal(err)
	}
	return events
}

func TestAuditEventsCantBeChangedOrDeleted(t *testing.T) {
	s := store(t)
	org := mustOrg(t, s)
	u := mustUser(t, s, "a@hospital.test")
	if err := s.RecordAudit(context.Background(), *event(org, u.ID, "patient_viewed")); err != nil {
		t.Fatal(err)
	}
	for _, q := range []string{
		`UPDATE audit_events SET action = 'nothing'`,
		`DELETE FROM audit_events`,
	} {
		_, err := s.pool.Exec(context.Background(), q)
		if err == nil || !strings.Contains(err.Error(), "append-only") {
			t.Errorf("%s: err = %v", q, err)
		}
	}
	if n := len(auditRows(t, s, org)); n != 1 {
		t.Errorf("%d rows, want 1", n)
	}
}

// The trail outlives what it names: purging a patient or an account
// keeps their events.
func TestAuditEventsOutliveTheirPatientAndActor(t *testing.T) {
	s := store(t)
	org := mustOrg(t, s)
	u := mustUser(t, s, "a@hospital.test")
	p, err := s.CreatePatient(context.Background(), org, u.ID, PatientFields{MRN: "SH-1", FullName: "Asha Verma"},
		event(org, u.ID, "patient_created"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.pool.Exec(context.Background(), `DELETE FROM patients WHERE id = $1`, p.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := s.pool.Exec(context.Background(), `DELETE FROM staff_users WHERE id = $1`, u.ID); err != nil {
		t.Fatal(err)
	}
	rows := auditRows(t, s, org)
	if len(rows) != 1 || *rows[0].PatientID != p.ID || *rows[0].TargetID != p.ID || rows[0].ActorName != nil {
		t.Errorf("rows = %+v", rows)
	}
}

// A change and its event commit together: when the event can't be
// written, the change is rolled back.
func TestAChangeRollsBackWhenItsEventFails(t *testing.T) {
	s := store(t)
	org := mustOrg(t, s)
	u := mustUser(t, s, "a@hospital.test")
	bad := event(org, u.ID, "Not A Valid Action") // refused by the action check

	if _, err := s.CreatePatient(context.Background(), org, u.ID, PatientFields{MRN: "SH-1", FullName: "A"}, bad); err == nil {
		t.Fatal("CreatePatient succeeded with a bad event")
	}
	if got, _ := s.ListPatients(context.Background(), org, ""); len(got) != 0 {
		t.Errorf("the patient was created: %+v", got)
	}

	p := mustPatient(t, s, org, u.ID, "SH-2")
	doc := mustDocument(t, s, p, u, "a.pdf")
	if _, err := s.SoftDeleteDocument(context.Background(), org, doc.ID, u.ID, time.Now(), bad); err == nil {
		t.Fatal("SoftDeleteDocument succeeded with a bad event")
	}
	if d, _ := s.GetDocument(context.Background(), org, doc.ID); d == nil {
		t.Error("the document was deleted")
	}
	if n := len(auditRows(t, s, org)); n != 0 {
		t.Errorf("%d rows written", n)
	}
}

func TestMutationsWriteTheirEvent(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	org := mustOrg(t, s)
	u := mustUser(t, s, "a@hospital.test")
	p, err := s.CreatePatient(ctx, org, u.ID, PatientFields{MRN: "SH-1", FullName: "A"}, event(org, u.ID, "patient_created"))
	if err != nil {
		t.Fatal(err)
	}
	doc, err := s.CreateDocument(ctx, org, NewDocument{PatientID: p.ID, UploadedBy: u.ID, OriginalName: "a.pdf",
		ContentType: "application/pdf", StoragePath: "a.pdf", SHA256: strings.Repeat("0", 64)}, event(org, u.ID, "document_uploaded"))
	if err != nil {
		t.Fatal(err)
	}
	// Changes that find nothing write nothing.
	if _, err := s.UpdatePatient(ctx, org, "00000000-0000-4000-8000-000000000000", time.Now(), PatientFields{MRN: "X", FullName: "X"},
		event(org, u.ID, "patient_updated")); err != nil {
		t.Fatal(err)
	}
	if _, err := s.SoftDeleteDocument(ctx, org, doc.ID, u.ID, time.Now(), event(org, u.ID, "document_deleted")); err != nil {
		t.Fatal(err)
	}
	if _, err := s.SoftDeleteDocument(ctx, org, doc.ID, u.ID, time.Now(), event(org, u.ID, "document_deleted")); err != nil {
		t.Fatal(err)
	}

	rows := auditRows(t, s, org)
	actions := []string{}
	for _, r := range rows {
		actions = append(actions, r.Action)
	}
	if strings.Join(actions, " ") != "document_deleted document_uploaded patient_created" {
		t.Fatalf("actions = %v", actions)
	}
	if up := rows[1]; *up.TargetID != doc.ID || *up.PatientID != p.ID || *up.ActorName != u.FullName ||
		up.IP != "192.0.2.7" {
		t.Errorf("upload event = %+v", up)
	}
}

func TestLoginEventsAndLockout(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	org := mustOrg(t, s)
	u := mustUser(t, s, "a@hospital.test")
	lockUntil := time.Now().Truncate(time.Second).Add(time.Hour + 123456789) // finer than the database keeps
	for range 2 {
		if _, err := s.RecordLoginFailure(ctx, u.ID, 2, lockUntil, event(org, u.ID, "login_failed")); err != nil {
			t.Fatal(err)
		}
	}
	rows := auditRows(t, s, org)
	if len(rows) != 3 || rows[0].Action != ActionAccountLocked || rows[1].Action != "login_failed" {
		t.Errorf("rows = %+v", rows)
	}
}

func TestLogoutAndPasswordSetupNameTheAccount(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	org := mustOrg(t, s)
	u := mustUser(t, s, "a@hospital.test")
	now := time.Now()
	token := []byte(strings.Repeat("t", 32))
	if err := s.CreateOneTimeToken(ctx, OneTimeToken{TokenHash: token, UserID: u.ID, Purpose: PurposeSetup,
		CreatedAt: now, ExpiresAt: now.Add(time.Hour)}, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := s.CompletePasswordSetup(ctx, token, "hash", now, &AuditEvent{Action: "password_set", Outcome: OutcomeSuccess}); err != nil {
		t.Fatal(err)
	}
	session := []byte(strings.Repeat("s", 32))
	if err := createSession(s, Session{TokenHash: session, UserID: u.ID, CreatedAt: now, LastSeenAt: now,
		ExpiresAt: now.Add(time.Hour)}); err != nil {
		t.Fatal(err)
	}
	if err := s.DeleteSession(ctx, session, &AuditEvent{Action: "logout", Outcome: OutcomeSuccess}); err != nil {
		t.Fatal(err)
	}
	// A logout without a session writes nothing.
	if err := s.DeleteSession(ctx, session, &AuditEvent{Action: "logout", Outcome: OutcomeSuccess}); err != nil {
		t.Fatal(err)
	}

	rows := auditRows(t, s, org)
	if len(rows) != 2 {
		t.Fatalf("rows = %+v", rows)
	}
	for _, r := range rows {
		if *r.ActorID != u.ID {
			t.Errorf("%s: actor %v", r.Action, r.ActorID)
		}
	}
}

func TestAPatientFilterFindsTheListingsThatShowedThem(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	org := mustOrg(t, s)
	a := mustUser(t, s, "a@hospital.test")
	p := mustPatient(t, s, org, a.ID, "SH-1")
	q := mustPatient(t, s, org, a.ID, "SH-2")
	queue := event(org, a.ID, "review_queue_viewed")
	queue.Details = map[string]any{"documents": 3, "patient_ids": []string{p.ID}}
	if err := s.RecordAudit(ctx, *queue); err != nil {
		t.Fatal(err)
	}
	for id, want := range map[string]int{p.ID: 1, q.ID: 0} {
		got, err := s.ListAuditEvents(ctx, org, AuditFilter{PatientID: id})
		if err != nil {
			t.Fatal(err)
		}
		if len(got) != want {
			t.Errorf("patient %s: %d events, want %d", id, len(got), want)
		}
	}
}

func TestListAuditEventsFiltersAndPages(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	org := mustOrg(t, s)
	a := mustUser(t, s, "a@hospital.test")
	b := mustUser(t, s, "b@hospital.test")
	otherOrg, them := mustOtherOrg(t, s)
	p := mustPatient(t, s, org, a.ID, "SH-1")
	day := time.Date(2026, 11, 10, 12, 0, 0, 0, time.UTC)

	write := func(ev *AuditEvent, at time.Time) {
		ev.At = at
		if err := s.RecordAudit(ctx, *ev); err != nil {
			t.Fatal(err)
		}
	}
	viewed := event(org, a.ID, "patient_viewed")
	viewed.PatientID = &p.ID
	write(viewed, day)
	write(event(org, b.ID, "patients_listed"), day.Add(24*time.Hour))
	write(event(org, a.ID, "logout"), day.Add(48*time.Hour))
	write(event(otherOrg, them.ID, "patients_listed"), day)

	count := func(f AuditFilter) int {
		t.Helper()
		got, err := s.ListAuditEvents(ctx, org, f)
		if err != nil {
			t.Fatal(err)
		}
		return len(got)
	}
	from, to := day.Add(24*time.Hour), day.Add(48*time.Hour)
	for name, c := range map[string]struct {
		f    AuditFilter
		want int
	}{
		"all of the organisation": {AuditFilter{}, 3},
		"by actor":                {AuditFilter{ActorID: a.ID}, 2},
		"by patient":              {AuditFilter{PatientID: p.ID}, 1},
		"from":                    {AuditFilter{From: &from}, 2},
		"to (exclusive)":          {AuditFilter{To: &to}, 2},
		"a page":                  {AuditFilter{Limit: 2}, 2},
	} {
		if got := count(c.f); got != c.want {
			t.Errorf("%s: %d events, want %d", name, got, c.want)
		}
	}
	all := auditRows(t, s, org)
	if all[0].Action != "logout" || all[2].Action != "patient_viewed" {
		t.Errorf("not newest first: %+v", all)
	}
	if got := count(AuditFilter{BeforeID: all[1].ID}); got != 1 {
		t.Errorf("before the second: %d, want 1", got)
	}
}
