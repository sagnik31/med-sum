package db

// Tests against a throwaway database (med_sum_test_go), created from the
// schema files before the tests and dropped after. They are skipped when
// Postgres or psql is unavailable; MEDSUM_REQUIRE_DB=1 makes that a failure.
// The admin connection comes from MEDSUM_TEST_ADMIN_URL (default: the local
// postgres database); med_sum itself is never touched.

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

const testDBName = "med_sum_test_go"

var (
	testStore   *Store
	unavailable string // why the database tests are skipped
)

func TestMain(m *testing.M) {
	cleanup, err := setupTestDB()
	if err != nil {
		if os.Getenv("MEDSUM_REQUIRE_DB") == "1" {
			fmt.Fprintln(os.Stderr, "database tests required:", err)
			os.Exit(1)
		}
		unavailable = err.Error()
	}
	code := m.Run()
	if cleanup != nil {
		cleanup()
	}
	os.Exit(code)
}

func setupTestDB() (func(), error) {
	psql, err := exec.LookPath("psql")
	if err != nil {
		return nil, fmt.Errorf("psql is not on PATH")
	}
	adminURL := os.Getenv("MEDSUM_TEST_ADMIN_URL")
	if adminURL == "" {
		adminURL = "postgres://postgres:postgres@localhost:5432/postgres?sslmode=disable"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	admin, err := pgx.Connect(ctx, adminURL)
	if err != nil {
		return nil, fmt.Errorf("postgres is unreachable: %w", err)
	}
	drop := func() {
		_, _ = admin.Exec(context.Background(), "DROP DATABASE IF EXISTS "+testDBName+" WITH (FORCE)")
	}
	drop()
	if _, err := admin.Exec(ctx, "CREATE DATABASE "+testDBName); err != nil {
		admin.Close(context.Background())
		return nil, fmt.Errorf("create %s: %w", testDBName, err)
	}
	cleanup := func() {
		if testStore != nil {
			testStore.Close()
		}
		drop()
		admin.Close(context.Background())
	}

	u, err := url.Parse(adminURL)
	if err != nil {
		cleanup()
		return nil, err
	}
	u.Path = "/" + testDBName
	testURL := u.String()

	_, thisFile, _, _ := runtime.Caller(0)
	migrations := filepath.Join(filepath.Dir(thisFile), "..", "..", "..", "db", "migrations")
	for _, f := range []string{"med_sum_schema.sql", "002_patient_summary_status.sql"} {
		out, err := exec.Command(psql, "-q", "-v", "ON_ERROR_STOP=1", "-d", testURL,
			"-f", filepath.Join(migrations, f)).CombinedOutput()
		if err != nil {
			cleanup()
			return nil, fmt.Errorf("load %s: %v\n%s", f, err, out)
		}
	}

	testStore, err = New(ctx, testURL)
	if err != nil {
		cleanup()
		return nil, err
	}
	return cleanup, nil
}

// store returns the test store on an empty database.
func store(t *testing.T) *Store {
	t.Helper()
	if unavailable != "" {
		t.Skip("database tests skipped: " + unavailable)
	}
	if _, err := testStore.pool.Exec(context.Background(), "TRUNCATE users, documents, insights CASCADE"); err != nil {
		t.Fatal(err)
	}
	return testStore
}

func strPtr(s string) *string { return &s }

func mustUser(t *testing.T, s *Store, phone string) *User {
	t.Helper()
	u, err := s.UpsertUserByPhone(context.Background(), phone, nil)
	if err != nil {
		t.Fatal(err)
	}
	return u
}

func mustDocument(t *testing.T, s *Store, userID, path string) *Document {
	t.Helper()
	d, err := s.CreateDocument(context.Background(), userID, "report.pdf", "application/pdf", path)
	if err != nil {
		t.Fatal(err)
	}
	return d
}

func setInsight(t *testing.T, s *Store, docID, status string, errMsg *string) {
	t.Helper()
	_, err := s.pool.Exec(context.Background(), `
INSERT INTO insights (document_id, user_id, status, error_message)
SELECT id, user_id, $2, $3 FROM documents WHERE id = $1`, docID, status, errMsg)
	if err != nil {
		t.Fatal(err)
	}
}

// ----- Users -----

func TestUpsertUserByPhoneCreatesThenUpdates(t *testing.T) {
	s := store(t)
	ctx := context.Background()

	first, err := s.UpsertUserByPhone(ctx, "+910000000001", strPtr("Asha Rao"))
	if err != nil {
		t.Fatal(err)
	}
	if first.FullName == nil || *first.FullName != "Asha Rao" || first.LastLoginAt == nil {
		t.Fatalf("created user = %+v", first)
	}

	// Logging in again without a name keeps the stored one.
	again, err := s.UpsertUserByPhone(ctx, "+910000000001", nil)
	if err != nil {
		t.Fatal(err)
	}
	if again.ID != first.ID || again.FullName == nil || *again.FullName != "Asha Rao" {
		t.Errorf("second login = %+v, want the same user and name", again)
	}
	if again.LastLoginAt.Before(*first.LastLoginAt) {
		t.Error("last_login_at went backwards")
	}

	renamed, _ := s.UpsertUserByPhone(ctx, "+910000000001", strPtr("Asha R."))
	if *renamed.FullName != "Asha R." {
		t.Errorf("full_name = %q, want the new name", *renamed.FullName)
	}
}

func TestGetPatientSummaryOfANewUserIsEmpty(t *testing.T) {
	s := store(t)
	u := mustUser(t, s, "+910000000001")

	p, err := s.GetPatientSummary(context.Background(), u.ID)
	if err != nil {
		t.Fatal(err)
	}
	if p.HTML != nil || p.Status != nil || p.Error != nil || p.UpdatedAt != nil {
		t.Errorf("summary = %+v, want all empty", p)
	}
}

// ----- Documents -----

func TestCreateDocument(t *testing.T) {
	s := store(t)
	u := mustUser(t, s, "+910000000001")

	d := mustDocument(t, s, u.ID, `D:\uploads\abc.pdf`)

	if d.ID == "" || d.UserID != u.ID || d.Status != "uploaded" || d.StoragePath != `D:\uploads\abc.pdf` {
		t.Errorf("document = %+v", d)
	}
	if time.Since(d.UploadedAt) > time.Minute {
		t.Errorf("uploaded_at = %v, want now", d.UploadedAt)
	}
}

func TestListDocumentsByUser(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "+910000000001")
	other := mustUser(t, s, "+910000000002")

	empty, err := s.ListDocumentsByUser(ctx, me.ID)
	if err != nil {
		t.Fatal(err)
	}
	if empty == nil || len(empty) != 0 {
		t.Errorf("empty list = %#v, want a non-nil empty slice", empty)
	}

	older := mustDocument(t, s, me.ID, "a.pdf")
	newer := mustDocument(t, s, me.ID, "b.pdf")
	mustDocument(t, s, other.ID, "c.pdf")
	_, _ = s.pool.Exec(ctx, "UPDATE documents SET uploaded_at = NOW() - interval '1 day' WHERE id = $1", older.ID)
	setInsight(t, s, older.ID, "completed", nil)

	docs, err := s.ListDocumentsByUser(ctx, me.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(docs) != 2 || docs[0].ID != newer.ID || docs[1].ID != older.ID {
		t.Fatalf("listed %v, want newest first and only mine", ids(docs))
	}
	if docs[0].InsightStatus != "not_started" || docs[1].InsightStatus != "completed" {
		t.Errorf("insight statuses = %q, %q", docs[0].InsightStatus, docs[1].InsightStatus)
	}
}

func ids(docs []Document) []string {
	out := make([]string, len(docs))
	for i, d := range docs {
		out[i] = d.ID
	}
	return out
}

func TestGetDocumentByIDAndUserChecksOwnership(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "+910000000001")
	other := mustUser(t, s, "+910000000002")
	d := mustDocument(t, s, me.ID, "a.pdf")

	got, err := s.GetDocumentByIDAndUser(ctx, d.ID, me.ID)
	if err != nil || got == nil || got.ID != d.ID {
		t.Fatalf("owner lookup = %+v, %v", got, err)
	}
	got, err = s.GetDocumentByIDAndUser(ctx, d.ID, other.ID)
	if err != nil || got != nil {
		t.Errorf("another user's lookup = %+v, %v; want nil, nil", got, err)
	}
}

func TestDeleteDocumentByIDAndUser(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "+910000000001")
	other := mustUser(t, s, "+910000000002")
	alone := mustDocument(t, s, me.ID, "alone.pdf")
	sharedA := mustDocument(t, s, me.ID, "shared.pdf")
	mustDocument(t, s, me.ID, "shared.pdf")
	setInsight(t, s, alone.ID, "completed", nil)

	path, shared, err := s.DeleteDocumentByIDAndUser(ctx, alone.ID, other.ID)
	if err != nil || path != "" {
		t.Fatalf("another user's delete = %q, %v; want nothing deleted", path, err)
	}

	path, shared, err = s.DeleteDocumentByIDAndUser(ctx, alone.ID, me.ID)
	if err != nil || path != "alone.pdf" || shared {
		t.Errorf("delete = %q, shared %v, %v", path, shared, err)
	}
	if ins, _ := s.GetInsightByDocumentID(ctx, alone.ID); ins != nil {
		t.Error("the deleted document's insight is still there")
	}

	path, shared, err = s.DeleteDocumentByIDAndUser(ctx, sharedA.ID, me.ID)
	if err != nil || path != "shared.pdf" || !shared {
		t.Errorf("delete of a shared file = %q, shared %v, %v; want shared", path, shared, err)
	}

	path, _, err = s.DeleteDocumentByIDAndUser(ctx, alone.ID, me.ID)
	if err != nil || path != "" {
		t.Errorf("second delete = %q, %v; want nothing", path, err)
	}
}

// ----- Insights -----

func TestGetInsightByDocumentID(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "+910000000001")
	d := mustDocument(t, s, me.ID, "a.pdf")

	ins, err := s.GetInsightByDocumentID(ctx, d.ID)
	if err != nil || ins != nil {
		t.Fatalf("before any job = %+v, %v; want nil, nil", ins, err)
	}

	setInsight(t, s, d.ID, "failed", strPtr("model down"))
	ins, err = s.GetInsightByDocumentID(ctx, d.ID)
	if err != nil {
		t.Fatal(err)
	}
	if ins.Status != "failed" || ins.ErrorMessage == nil || *ins.ErrorMessage != "model down" || ins.HTMLInsights != "" {
		t.Errorf("insight = %+v", ins)
	}
}
