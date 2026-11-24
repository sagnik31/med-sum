package db

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

// apiRoleStore creates a role with db/api_role.sql's grants (under a
// name of its own, dropped after the test) and returns a store and a raw
// connection that use it.
func apiRoleStore(t *testing.T, s *Store) (*Store, *pgx.Conn) {
	t.Helper()
	ctx := context.Background()
	var canCreate bool
	if err := s.pool.QueryRow(ctx,
		`SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user`).Scan(&canCreate); err != nil {
		t.Fatal(err)
	}
	if !canCreate {
		t.Skip("the test connection can't create roles")
	}
	b := make([]byte, 6)
	_, _ = rand.Read(b)
	name := "medsum_api_test_" + hex.EncodeToString(b)
	password := hex.EncodeToString(b) + "-password"

	_, thisFile, _, _ := runtime.Caller(0)
	sqlFile := filepath.Join(filepath.Dir(thisFile), "..", "..", "..", "db", "api_role.sql")
	text, err := os.ReadFile(sqlFile)
	if err != nil {
		t.Fatal(err)
	}
	// Simple protocol: the file holds several statements.
	if _, err := s.pool.Exec(ctx, strings.ReplaceAll(string(text), "medsum_api", name), pgx.QueryExecModeSimpleProtocol); err != nil {
		t.Fatalf("api_role.sql: %v", err)
	}
	if _, err := s.pool.Exec(ctx, `ALTER ROLE `+name+` PASSWORD '`+password+`'`); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = s.pool.Exec(context.Background(), `DROP OWNED BY `+name)
		_, _ = s.pool.Exec(context.Background(), `DROP ROLE `+name)
	})

	u, err := url.Parse(testDBURL)
	if err != nil {
		t.Fatal(err)
	}
	u.User = url.UserPassword(name, password)
	roleStore, err := New(ctx, u.String())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(roleStore.Close)
	conn, err := pgx.Connect(ctx, u.String())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close(context.Background()) })
	return roleStore, conn
}

func ev(orgID, actorID, action string) *AuditEvent {
	return &AuditEvent{OrgID: &orgID, ActorID: &actorID, Action: action, Outcome: OutcomeSuccess, IP: "192.0.2.1"}
}

// Every query the API makes runs as its role.
func TestTheAPIRoleCanRunTheAPIsQueries(t *testing.T) {
	owner := store(t)
	s, _ := apiRoleStore(t, owner)
	ctx := context.Background()
	org := mustOrg(t, owner) // medsumctl creates organisations, as the owner
	now := time.Now().Truncate(time.Microsecond)
	check := func(what string, err error) {
		t.Helper()
		if err != nil {
			t.Fatalf("%s: %v", what, err)
		}
	}

	if _, err := s.ListOrganizations(ctx); err != nil {
		t.Fatal(err)
	}
	admin, err := s.CreateUser(ctx, NewUser{OrgID: org, Email: "admin@hospital.test", FullName: "Admin",
		Roles: []string{RoleAdmin}}, nil)
	check("CreateUser", err)
	u, err := s.CreateUser(ctx, NewUser{OrgID: org, Email: "dr@hospital.test", FullName: "Dr A",
		Roles: []string{RoleClinician}, CreatedBy: &admin.ID}, ev(org, admin.ID, "staff_invited"))
	check("CreateUser", err)
	token := []byte(strings.Repeat("t", 32))
	check("CreateOneTimeToken", s.CreateOneTimeToken(ctx, OneTimeToken{TokenHash: token, UserID: u.ID,
		Purpose: PurposeSetup, CreatedAt: now, ExpiresAt: now.Add(time.Hour), CreatedBy: &admin.ID}, ev(org, admin.ID, "link_issued")))
	if got, err := s.GetUsableToken(ctx, token, now); err != nil || got == nil {
		t.Fatalf("GetUsableToken: %v %v", got, err)
	}
	_, err = s.CompletePasswordSetup(ctx, token, "hash", now, &AuditEvent{Action: "password_set", Outcome: OutcomeSuccess})
	check("CompletePasswordSetup", err)
	_, err = s.GetUserByEmail(ctx, "dr@hospital.test")
	check("GetUserByEmail", err)
	_, err = s.GetUserByID(ctx, u.ID)
	check("GetUserByID", err)
	_, err = s.RecordLoginFailure(ctx, u.ID, 5, now.Add(time.Minute), ev(org, u.ID, "login_failed"))
	check("RecordLoginFailure", err)
	check("RecordLogin", s.RecordLogin(ctx, Login{UserID: u.ID, VerifiedHash: "hash", At: now, Session: Session{
		TokenHash: []byte(strings.Repeat("l", 32)), UserID: u.ID, CreatedAt: now, LastSeenAt: now,
		ExpiresAt: now.Add(time.Hour)}}, ev(org, u.ID, "login_succeeded")))
	check("ChangePassword", s.ChangePassword(ctx, u.ID, "hash", "hash1", nil, now, ev(org, u.ID, "password_changed")))
	check("SetPasswordHash", s.SetPasswordHash(ctx, u.ID, "hash2", now, ev(org, u.ID, "password_changed")))

	session := []byte(strings.Repeat("s", 32))
	other := []byte(strings.Repeat("o", 32))
	for _, h := range [][]byte{session, other} {
		check("insertSession", createSession(s, Session{TokenHash: h, UserID: u.ID, CreatedAt: now,
			LastSeenAt: now, ExpiresAt: now.Add(time.Hour), IP: "192.0.2.1"}))
	}
	_, err = s.GetSession(ctx, session)
	check("GetSession", err)
	check("TouchSession", s.TouchSession(ctx, session, now.Add(time.Minute)))
	sessions, err := s.ListUserSessions(ctx, u.ID)
	check("ListUserSessions", err)
	_, err = s.DeleteUserSession(ctx, u.ID, sessions[len(sessions)-1].ID, ev(org, u.ID, "session_ended"))
	check("DeleteUserSession", err)
	check("DeleteUserSessions", s.DeleteUserSessions(ctx, u.ID, session, ev(org, u.ID, "other_sessions_ended")))
	_, err = s.DeleteExpiredSessions(ctx, now, time.Hour)
	check("DeleteExpiredSessions", err)

	_, err = s.ListUsers(ctx, org)
	check("ListUsers", err)
	roles := []string{RoleClinician, RoleAdmin}
	_, err = s.UpdateUser(ctx, UserUpdate{OrgID: org, UserID: u.ID, Roles: roles}, ev(org, admin.ID, "staff_updated"))
	check("UpdateUser", err)

	p, err := s.CreatePatient(ctx, org, u.ID, PatientFields{MRN: "SH-1", FullName: "A"}, ev(org, u.ID, "patient_created"))
	check("CreatePatient", err)
	_, err = s.UpdatePatient(ctx, org, p.ID, PatientFields{MRN: "SH-1", FullName: "B"}, ev(org, u.ID, "patient_updated"))
	check("UpdatePatient", err)
	_, err = s.GetPatient(ctx, org, p.ID)
	check("GetPatient", err)
	_, err = s.ListPatients(ctx, org, "b")
	check("ListPatients", err)
	_, err = s.GetPatientSummary(ctx, p.ID)
	check("GetPatientSummary", err)
	doc, err := s.CreateDocument(ctx, org, NewDocument{PatientID: p.ID, UploadedBy: u.ID, OriginalName: "a.pdf",
		ContentType: "application/pdf", StoragePath: "a.pdf", SHA256: strings.Repeat("0", 64)}, ev(org, u.ID, "document_uploaded"))
	check("CreateDocument", err)
	_, err = s.GetDocument(ctx, org, doc.ID)
	check("GetDocument", err)
	_, err = s.ListPatientDocuments(ctx, org, p.ID)
	check("ListPatientDocuments", err)
	_, err = s.GetInsightByDocumentID(ctx, doc.ID)
	check("GetInsightByDocumentID", err)
	_, err = s.GetDocumentRecord(ctx, doc.ID)
	check("GetDocumentRecord", err)
	// A review: the AI service wrote the insight and the record (as the owner here).
	if _, err := owner.pool.Exec(ctx, `INSERT INTO insights (document_id, status) VALUES ($1, 'needs_review')`, doc.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := owner.pool.Exec(ctx, `INSERT INTO document_records (document_id, schema_version, record, updated_at)
VALUES ($1, 1, '{"schema_version": 1}', $2)`, doc.ID, now); err != nil {
		t.Fatal(err)
	}
	_, err = s.GetProcessing(ctx, doc.ID)
	check("GetProcessing", err)
	_, _, err = s.GetDocumentTrace(ctx, doc.ID, "")
	check("GetDocumentTrace", err)
	_, err = s.ListOrgRuns(ctx, org, nil)
	check("ListOrgRuns", err)
	_, _, err = s.GetOrgRun(ctx, org, "00000000-0000-0000-0000-000000000000")
	check("GetOrgRun", err)
	_, err = s.ListReviewQueue(ctx, org)
	check("ListReviewQueue", err)
	_, err = s.ApplyReview(ctx, org, doc.ID, now, RecordReview{Record: []byte(`{"schema_version": 1}`),
		HTML: "<p></p>", Verified: true, ItemID: "obs-1", Action: "confirm", ReviewerID: u.ID, At: now.Add(time.Second)},
		ev(org, u.ID, "field_confirmed"))
	check("ApplyReview", err)
	// A document that passed every check, signed off.
	if _, err := owner.pool.Exec(ctx, `UPDATE insights SET status = 'checks_passed' WHERE document_id = $1`, doc.ID); err != nil {
		t.Fatal(err)
	}
	check("SignOff", s.SignOff(ctx, org, doc.ID, now.Add(time.Second), now, ev(org, u.ID, "document_signed_off")))
	if _, err := owner.pool.Exec(ctx, `UPDATE insights SET status = 'checks_passed' WHERE document_id = $1`, doc.ID); err != nil {
		t.Fatal(err)
	}
	// An audit sample of the record's run (the AI service adds them; the
	// owner here), finished.
	if _, err := owner.pool.Exec(ctx, `WITH r AS (INSERT INTO pipeline_runs (document_id, graph_version) VALUES ($1, 'test') RETURNING id),
rec AS (UPDATE document_records SET run_id = (SELECT id FROM r) WHERE document_id = $1)
INSERT INTO audit_samples (run_id, document_id) SELECT id, $1 FROM r`, doc.ID); err != nil {
		t.Fatal(err)
	}
	check("CompleteAudit", s.CompleteAudit(ctx, org, doc.ID, u.ID, now, ev(org, u.ID, "audit_completed")))
	ok, err := s.SoftDeleteDocument(ctx, org, doc.ID, u.ID, now, ev(org, u.ID, "document_deleted"))
	if err != nil || !ok {
		t.Fatalf("SoftDeleteDocument: %v %v", ok, err)
	}

	check("RecordAudit", s.RecordAudit(ctx, *ev(org, u.ID, "patient_viewed")))
	events, err := s.ListAuditEvents(ctx, org, AuditFilter{PatientID: p.ID})
	check("ListAuditEvents", err)
	if len(events) == 0 {
		t.Error("no audit events listed")
	}
	check("DeleteSession", s.DeleteSession(ctx, session, &AuditEvent{Action: "logout", Outcome: OutcomeSuccess}))
}

// What the role must not do: change the schema, hard-delete patient
// data, rewrite the audit log, or read the AI service's tables.
func TestTheAPIRoleIsRefusedTheRest(t *testing.T) {
	owner := store(t)
	_, conn := apiRoleStore(t, owner)
	org := mustOrg(t, owner)
	u := mustUser(t, owner, "a@hospital.test")
	p := mustPatient(t, owner, org, u.ID, "SH-1")
	mustDocument(t, owner, p, u, "a.pdf")
	if err := owner.RecordAudit(context.Background(), *ev(org, u.ID, "patient_viewed")); err != nil {
		t.Fatal(err)
	}

	for _, q := range []string{
		`CREATE TABLE x (id int)`,
		`DROP TABLE documents`,
		`ALTER TABLE patients ADD COLUMN x int`,
		`TRUNCATE audit_events`,
		`UPDATE audit_events SET action = 'x'`,
		`DELETE FROM audit_events`,
		`DELETE FROM documents`,
		`DELETE FROM patients`,
		`DELETE FROM staff_users`,
		`DELETE FROM organizations`,
		`INSERT INTO organizations (name) VALUES ('x')`,
		`UPDATE organizations SET name = 'x'`,
		`UPDATE patients SET org_id = org_id`,
		`UPDATE staff_users SET org_id = org_id`,
		`UPDATE staff_users SET email = 'x@y.test'`,
		`UPDATE documents SET extracted_markdown = 'x'`,
		`UPDATE documents SET patient_id = patient_id`,
		`UPDATE insights SET error_message = 'x'`,
		`UPDATE insights SET document_id = document_id`,
		`UPDATE document_records SET run_id = NULL`,
		`UPDATE record_reviews SET corrected = 'x'`,
		`DELETE FROM record_reviews`,
		`INSERT INTO audit_samples (run_id, document_id) VALUES (gen_random_uuid(), gen_random_uuid())`,
		`UPDATE audit_samples SET run_id = run_id`,
		`DELETE FROM audit_samples`,
		`SELECT * FROM review_signal_precision`,
		`DELETE FROM document_records`,
		`INSERT INTO patient_summaries (patient_id, status) SELECT id, 'failed' FROM patients`,
		`SELECT * FROM jobs`,
		`SELECT * FROM pipeline_steps`,
		`SELECT * FROM pipeline_model_calls`,
		`SELECT * FROM prompts`,
	} {
		_, err := conn.Exec(context.Background(), q)
		var pgErr *pgconn.PgError
		if !errors.As(err, &pgErr) || pgErr.Code != "42501" { // insufficient_privilege
			t.Errorf("%s: err = %v, want permission denied", q, err)
		}
	}
}
