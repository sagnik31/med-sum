package db

// Tests against a throwaway database (med_sum_test_go), created from the
// schema files before the tests and dropped after. They are skipped when
// Postgres or psql is unavailable; MEDSUM_REQUIRE_DB=1 makes that a failure.
// The admin connection comes from MEDSUM_TEST_ADMIN_URL (default: the local
// postgres database); med_sum itself is never touched.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

const testDBName = "med_sum_test_go"

var (
	testStore   *Store
	testDBURL   string // the throwaway database, as the admin connection's role
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
	testDBURL = testURL

	_, thisFile, _, _ := runtime.Caller(0)
	schema := filepath.Join(filepath.Dir(thisFile), "..", "..", "..", "db", "schema.sql")
	out, err := exec.Command(psql, "-q", "-v", "ON_ERROR_STOP=1", "-d", testURL,
		"-f", schema).CombinedOutput()
	if err != nil {
		cleanup()
		return nil, fmt.Errorf("load %s: %v\n%s", schema, err, out)
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
	if _, err := testStore.pool.Exec(context.Background(), "TRUNCATE organizations, staff_users, documents, insights, audit_events CASCADE"); err != nil {
		t.Fatal(err)
	}
	return testStore
}

func strPtr(s string) *string { return &s }

// mustOrg returns the test organisation, creating it on first use.
func mustOrg(t *testing.T, s *Store) string {
	t.Helper()
	orgs, err := s.ListOrganizations(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	for _, o := range orgs {
		if o.Name == "Test Hospital" {
			return o.ID
		}
	}
	o, err := s.CreateOrganization(context.Background(), "Test Hospital")
	if err != nil {
		t.Fatal(err)
	}
	return o.ID
}

// mustUser creates an invited clinician account.
func mustUser(t *testing.T, s *Store, email string) *User {
	t.Helper()
	u, err := s.CreateUser(context.Background(), NewUser{
		OrgID: mustOrg(t, s), Email: email, FullName: "Dr " + email, Roles: []string{RoleClinician},
	}, nil)
	if err != nil {
		t.Fatal(err)
	}
	return u
}

// mustPatient creates a patient in the organisation, created by the account.
func mustPatient(t *testing.T, s *Store, orgID, createdBy, mrn string) *Patient {
	t.Helper()
	p, err := s.CreatePatient(context.Background(), orgID, createdBy, PatientFields{MRN: mrn, FullName: "Patient " + mrn}, nil)
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// mustOtherOrg returns a second organisation with a clinician in it.
func mustOtherOrg(t *testing.T, s *Store) (orgID string, clinician *User) {
	t.Helper()
	o, err := s.CreateOrganization(context.Background(), "Other Hospital")
	if err != nil {
		t.Fatal(err)
	}
	u, err := s.CreateUser(context.Background(), NewUser{
		OrgID: o.ID, Email: "elsewhere@other.test", FullName: "Dr Elsewhere", Roles: []string{RoleClinician},
	}, nil)
	if err != nil {
		t.Fatal(err)
	}
	return o.ID, u
}

func mustDocument(t *testing.T, s *Store, p *Patient, uploader *User, path string) *Document {
	t.Helper()
	d, err := s.CreateDocument(context.Background(), p.OrgID, NewDocument{
		PatientID: p.ID, UploadedBy: uploader.ID, OriginalName: "report.pdf",
		ContentType: "application/pdf", StoragePath: path, SHA256: fileHash(path),
	}, nil)
	if err != nil || d == nil {
		t.Fatalf("CreateDocument = %+v, %v", d, err)
	}
	return d
}

// fileHash stands in for a file's SHA-256: a distinct one per path.
func fileHash(path string) string {
	sum := sha256.Sum256([]byte(path))
	return hex.EncodeToString(sum[:])
}

func setInsight(t *testing.T, s *Store, docID, status string, errMsg *string) {
	t.Helper()
	_, err := s.pool.Exec(context.Background(), `
INSERT INTO insights (document_id, status, error_message) VALUES ($1, $2, $3)`, docID, status, errMsg)
	if err != nil {
		t.Fatal(err)
	}
}

// ----- Patients -----

func TestCreateAndUpdatePatient(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	age := 54
	p, err := s.CreatePatient(ctx, me.OrgID, me.ID, PatientFields{
		MRN: "SH-1", FullName: "Meera Iyer", DateOfBirth: strPtr("1967-04-12"), Sex: strPtr("F"),
	}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if p.ID == "" || p.OrgID != me.OrgID || p.MRN != "SH-1" || p.DateOfBirth == nil || *p.DateOfBirth != "1967-04-12" ||
		p.Sex == nil || *p.Sex != "F" || p.AgeYears != nil || p.CreatedBy == nil || *p.CreatedBy != me.ID {
		t.Errorf("patient = %+v", p)
	}

	if _, err := s.CreatePatient(ctx, me.OrgID, me.ID, PatientFields{MRN: "SH-1", FullName: "Someone"}, nil); err != ErrMRNTaken {
		t.Errorf("same MRN again: err = %v, want ErrMRNTaken", err)
	}
	// Another organisation may use the same MRN.
	otherOrg, them := mustOtherOrg(t, s)
	if _, err := s.CreatePatient(ctx, otherOrg, them.ID, PatientFields{MRN: "SH-1", FullName: "Someone"}, nil); err != nil {
		t.Errorf("same MRN in another organisation: %v", err)
	}

	up, err := s.UpdatePatient(ctx, me.OrgID, p.ID, p.UpdatedAt, PatientFields{MRN: "SH-1", FullName: "Meera R Iyer", AgeYears: &age}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if up.FullName != "Meera R Iyer" || up.DateOfBirth != nil || up.AgeYears == nil || *up.AgeYears != 54 || !up.UpdatedAt.After(p.UpdatedAt) {
		t.Errorf("updated = %+v", up)
	}
	if got, err := s.UpdatePatient(ctx, otherOrg, p.ID, p.UpdatedAt, PatientFields{MRN: "X", FullName: "X"}, nil); err != nil || got != nil {
		t.Errorf("update from another organisation = %+v, %v; want nil, nil", got, err)
	}
}

func TestAPatientIsUpdatedOnlyAsItWasRead(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "a@hospital.test")
	p, err := s.CreatePatient(ctx, me.OrgID, me.ID, PatientFields{MRN: "SYN-123", FullName: "A"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.UpdatePatient(ctx, me.OrgID, p.ID, p.UpdatedAt, PatientFields{MRN: "SYN-124", FullName: "A"}, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := s.UpdatePatient(ctx, me.OrgID, p.ID, p.UpdatedAt, PatientFields{MRN: "SYN-123", FullName: "B"}, nil); !errors.Is(err, ErrPatientChanged) {
		t.Errorf("an update from the older read: %v, want ErrPatientChanged", err)
	}
	if got, _ := s.GetPatient(ctx, me.OrgID, p.ID); got.MRN != "SYN-124" || got.FullName != "A" {
		t.Errorf("patient = %+v", got)
	}
}

func TestAnMRNThatDiffersOnlyInCaseOrSpacingIsTaken(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "a@hospital.test")
	for _, mrn := range []string{"SYN-123", "SYN-124"} {
		if _, err := s.CreatePatient(ctx, me.OrgID, me.ID, PatientFields{MRN: mrn, FullName: "A"}, nil); err != nil {
			t.Fatal(err)
		}
	}
	for _, mrn := range []string{"syn-123", "SYN -123", "Syn - 123"} {
		if _, err := s.CreatePatient(ctx, me.OrgID, me.ID, PatientFields{MRN: mrn, FullName: "B"}, nil); !errors.Is(err, ErrMRNTaken) {
			t.Errorf("%q: %v, want ErrMRNTaken", mrn, err)
		}
	}
}

func TestPatientDetailsAreChecked(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	age := 30
	bad := []PatientFields{
		{MRN: "", FullName: "No MRN"},
		{MRN: " SH-2", FullName: "Padded MRN"},
		{MRN: "SH-3", FullName: ""},
		{MRN: "SH-4", FullName: "Both", DateOfBirth: strPtr("1990-01-01"), AgeYears: &age},
		{MRN: "SH-5", FullName: "Bad sex", Sex: strPtr("female")},
	}
	for _, f := range bad {
		if _, err := s.CreatePatient(ctx, me.OrgID, me.ID, f, nil); err == nil {
			t.Errorf("accepted %+v", f)
		}
	}
}

func TestGetAndListPatientsStayInTheOrganisation(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	meera, err := s.CreatePatient(ctx, me.OrgID, me.ID, PatientFields{MRN: "SH-100231", FullName: "Meera Iyer"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	rohan, err := s.CreatePatient(ctx, me.OrgID, me.ID, PatientFields{MRN: "SH-100232", FullName: "Rohan Kulkarni"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	otherOrg, them := mustOtherOrg(t, s)
	theirs := mustPatient(t, s, otherOrg, them.ID, "OT-1")

	if got, err := s.GetPatient(ctx, me.OrgID, meera.ID); err != nil || got == nil || got.ID != meera.ID {
		t.Errorf("GetPatient = %+v, %v", got, err)
	}
	if got, err := s.GetPatient(ctx, me.OrgID, theirs.ID); err != nil || got != nil {
		t.Errorf("another organisation's patient = %+v, %v; want nil, nil", got, err)
	}

	cases := map[string][]string{
		"":          {meera.ID, rohan.ID}, // by name
		"rohan":     {rohan.ID},
		"KULK":      {rohan.ID},
		"sh-100231": {meera.ID},
		"100231":    {}, // the MRN matches from its start only
		"OT-1":      {},
		"%":         {},
	}
	for q, want := range cases {
		got, err := s.ListPatients(ctx, me.OrgID, q)
		if err != nil {
			t.Fatal(err)
		}
		gotIDs := []string{}
		for _, p := range got {
			gotIDs = append(gotIDs, p.ID)
		}
		if fmt.Sprint(gotIDs) != fmt.Sprint(want) {
			t.Errorf("ListPatients(%q) = %v, want %v", q, gotIDs, want)
		}
	}
}

func TestGetPatientSummaryOfANewPatientIsEmpty(t *testing.T) {
	s := store(t)
	me := mustUser(t, s, "me@hospital.test")
	p := mustPatient(t, s, me.OrgID, me.ID, "SH-1")

	sum, err := s.GetPatientSummary(context.Background(), p.ID)
	if err != nil {
		t.Fatal(err)
	}
	if sum.HTML != nil || sum.Status != nil || sum.Error != nil || sum.UpdatedAt != nil {
		t.Errorf("summary = %+v, want all empty", sum)
	}
	_, err = s.pool.Exec(context.Background(),
		`INSERT INTO patient_summaries (patient_id, html, status) VALUES ($1, '<p>s</p>', 'completed')`, p.ID)
	if err != nil {
		t.Fatal(err)
	}
	sum, err = s.GetPatientSummary(context.Background(), p.ID)
	if err != nil || sum.HTML == nil || *sum.HTML != "<p>s</p>" || sum.Status == nil || *sum.Status != "completed" {
		t.Errorf("summary = %+v, %v", sum, err)
	}
}

// ----- Documents -----

func TestCreateDocument(t *testing.T) {
	s := store(t)
	me := mustUser(t, s, "me@hospital.test")
	p := mustPatient(t, s, me.OrgID, me.ID, "SH-1")

	d := mustDocument(t, s, p, me, `D:\uploads\abc.pdf`)

	if d.ID == "" || d.PatientID != p.ID || d.UploadedBy == nil || *d.UploadedBy != me.ID ||
		d.UploadedByName == nil || *d.UploadedByName != me.FullName || d.StoragePath != `D:\uploads\abc.pdf` {
		t.Errorf("document = %+v", d)
	}
	if time.Since(d.UploadedAt) > time.Minute {
		t.Errorf("uploaded_at = %v, want now", d.UploadedAt)
	}
}

func TestCreateDocumentRefusesAnotherOrganisationsPatient(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	otherOrg, them := mustOtherOrg(t, s)
	theirs := mustPatient(t, s, otherOrg, them.ID, "OT-1")

	d, err := s.CreateDocument(ctx, me.OrgID, NewDocument{
		PatientID: theirs.ID, UploadedBy: me.ID, OriginalName: "a.pdf", ContentType: "application/pdf",
		StoragePath: "a.pdf", SHA256: fileHash("a.pdf"),
	}, nil)
	if err != nil || d != nil {
		t.Errorf("upload to another organisation's patient = %+v, %v; want nil, nil", d, err)
	}
	var n int
	_ = s.pool.QueryRow(ctx, "SELECT count(*) FROM documents").Scan(&n)
	if n != 0 {
		t.Errorf("%d documents stored", n)
	}
}

func TestListPatientDocuments(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	p := mustPatient(t, s, me.OrgID, me.ID, "SH-1")
	other := mustPatient(t, s, me.OrgID, me.ID, "SH-2")

	empty, err := s.ListPatientDocuments(ctx, me.OrgID, p.ID)
	if err != nil {
		t.Fatal(err)
	}
	if empty == nil || len(empty) != 0 {
		t.Errorf("empty list = %#v, want a non-nil empty slice", empty)
	}

	older := mustDocument(t, s, p, me, "a.pdf")
	newer := mustDocument(t, s, p, me, "b.pdf")
	mustDocument(t, s, other, me, "c.pdf")
	_, _ = s.pool.Exec(ctx, "UPDATE documents SET uploaded_at = NOW() - interval '1 day' WHERE id = $1", older.ID)
	setInsight(t, s, older.ID, "verified", nil)

	docs, err := s.ListPatientDocuments(ctx, me.OrgID, p.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(docs) != 2 || docs[0].ID != newer.ID || docs[1].ID != older.ID {
		t.Fatalf("listed %v, want newest first and only this patient's", ids(docs))
	}
	if docs[0].InsightStatus != "not_started" || docs[1].InsightStatus != "verified" {
		t.Errorf("insight statuses = %q, %q", docs[0].InsightStatus, docs[1].InsightStatus)
	}
	otherOrg, _ := mustOtherOrg(t, s)
	if docs, err := s.ListPatientDocuments(ctx, otherOrg, p.ID); err != nil || len(docs) != 0 {
		t.Errorf("listed from another organisation: %v, %v", ids(docs), err)
	}
}

func TestAnExactCopyIsFlaggedForTheSamePatientOnly(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	mine := mustPatient(t, s, me.OrgID, me.ID, "SH-1")
	other := mustPatient(t, s, me.OrgID, me.ID, "SH-2")
	file := fileHash("the same file")
	create := func(p *Patient, path string) *Document {
		d, err := s.CreateDocument(ctx, me.OrgID, NewDocument{
			PatientID: p.ID, UploadedBy: me.ID, OriginalName: "report.pdf",
			ContentType: "application/pdf", StoragePath: path, SHA256: file,
		}, nil)
		if err != nil {
			t.Fatal(err)
		}
		return d
	}

	first := create(mine, "a.pdf")
	if first.DuplicateOf != nil || first.SHA256 != file {
		t.Fatalf("first upload = %+v, want no duplicate and its hash", first)
	}
	copy1 := create(mine, "b.pdf")
	copy2 := create(mine, "c.pdf")
	theirs := create(other, "d.pdf")
	for _, d := range []*Document{copy1, copy2} {
		if d.DuplicateOf == nil || *d.DuplicateOf != first.ID {
			t.Errorf("copy %s duplicate_of = %v, want %s", d.ID, d.DuplicateOf, first.ID)
		}
	}
	if theirs.DuplicateOf != nil {
		t.Errorf("another patient's copy is flagged: %v", *theirs.DuplicateOf)
	}

	docs, err := s.ListPatientDocuments(ctx, me.OrgID, mine.ID)
	if err != nil {
		t.Fatal(err)
	}
	flagged := map[string]string{}
	for _, d := range docs {
		if d.DuplicateOf != nil {
			flagged[d.ID] = *d.DuplicateOf
		}
	}
	want := map[string]string{copy1.ID: first.ID, copy2.ID: first.ID}
	if fmt.Sprint(flagged) != fmt.Sprint(want) {
		t.Errorf("listed duplicates = %v, want %v", flagged, want)
	}

	// Once the first is deleted, the next copy is the original.
	if ok, err := s.SoftDeleteDocument(ctx, me.OrgID, first.ID, me.ID, time.Now(), nil); err != nil || !ok {
		t.Fatal(ok, err)
	}
	docs, err = s.ListPatientDocuments(ctx, me.OrgID, mine.ID)
	if err != nil {
		t.Fatal(err)
	}
	for _, d := range docs {
		dup := d.DuplicateOf != nil
		if (d.ID == copy1.ID && dup) || (d.ID == copy2.ID && (!dup || *d.DuplicateOf != copy1.ID)) {
			t.Errorf("after deleting the first: %s duplicate_of = %v", d.ID, d.DuplicateOf)
		}
	}
}

func ids(docs []Document) []string {
	out := make([]string, len(docs))
	for i, d := range docs {
		out[i] = d.ID
	}
	return out
}

func TestGetDocumentChecksTheOrganisation(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	colleague := mustUser(t, s, "colleague@hospital.test")
	p := mustPatient(t, s, me.OrgID, me.ID, "SH-1")
	d := mustDocument(t, s, p, me, "a.pdf")
	otherOrg, _ := mustOtherOrg(t, s)

	got, err := s.GetDocument(ctx, colleague.OrgID, d.ID)
	if err != nil || got == nil || got.ID != d.ID {
		t.Fatalf("a colleague's lookup = %+v, %v; every clinician sees every patient", got, err)
	}
	got, err = s.GetDocument(ctx, otherOrg, d.ID)
	if err != nil || got != nil {
		t.Errorf("another organisation's lookup = %+v, %v; want nil, nil", got, err)
	}
}

func TestSoftDeleteDocument(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	p := mustPatient(t, s, me.OrgID, me.ID, "SH-1")
	d := mustDocument(t, s, p, me, "alone.pdf")
	setInsight(t, s, d.ID, "verified", nil)
	if _, err := s.pool.Exec(ctx, "INSERT INTO patient_summaries (patient_id, html, status) VALUES ($1, 'its values', 'completed')", p.ID); err != nil {
		t.Fatal(err)
	}
	otherOrg, them := mustOtherOrg(t, s)
	now := time.Now().Truncate(time.Microsecond)

	if ok, err := s.SoftDeleteDocument(ctx, otherOrg, d.ID, them.ID, now, nil); err != nil || ok {
		t.Fatalf("delete from another organisation = %v, %v; want nothing deleted", ok, err)
	}
	if ok, err := s.SoftDeleteDocument(ctx, me.OrgID, d.ID, me.ID, now, nil); err != nil || !ok {
		t.Fatalf("delete = %v, %v", ok, err)
	}
	if got, _ := s.GetDocument(ctx, me.OrgID, d.ID); got != nil {
		t.Error("a deleted document is still found")
	}
	if docs, _ := s.ListPatientDocuments(ctx, me.OrgID, p.ID); len(docs) != 0 {
		t.Errorf("a deleted document is still listed: %v", ids(docs))
	}
	// The summary page showed its values: it goes in the same transaction.
	if sum, err := s.GetPatientSummary(ctx, p.ID); err != nil || sum.HTML != nil {
		t.Errorf("summary after the delete = %+v, %v; want no page", sum, err)
	}
	// Soft: the row, its insight and who deleted it stay.
	var at time.Time
	var by string
	if err := s.pool.QueryRow(ctx, "SELECT deleted_at, deleted_by FROM documents WHERE id = $1", d.ID).Scan(&at, &by); err != nil {
		t.Fatal(err)
	}
	if !at.Equal(now) || by != me.ID {
		t.Errorf("deleted_at %v, deleted_by %s", at, by)
	}
	if ins, _ := s.GetInsightByDocumentID(ctx, d.ID); ins == nil {
		t.Error("the deleted document's insight is gone")
	}
	if ok, err := s.SoftDeleteDocument(ctx, me.OrgID, d.ID, me.ID, now, nil); err != nil || ok {
		t.Errorf("second delete = %v, %v; want nothing", ok, err)
	}
}

// ----- Insights -----

func TestGetInsightByDocumentID(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	d := mustDocument(t, s, mustPatient(t, s, me.OrgID, me.ID, "SH-1"), me, "a.pdf")

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

// ----- Records -----

func TestGetDocumentRecord(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	d := mustDocument(t, s, mustPatient(t, s, me.OrgID, me.ID, "SH-1"), me, "a.pdf")

	rec, err := s.GetDocumentRecord(ctx, d.ID)
	if err != nil || rec != nil {
		t.Fatalf("before any record = %+v, %v; want nil, nil", rec, err)
	}

	_, err = s.pool.Exec(ctx, `
INSERT INTO document_records (document_id, schema_version, record)
VALUES ($1, 1, '{"schema_version": 1, "observations": [{"id": "obs-1"}]}')`, d.ID)
	if err != nil {
		t.Fatal(err)
	}
	rec, err = s.GetDocumentRecord(ctx, d.ID)
	if err != nil {
		t.Fatal(err)
	}
	if rec.DocumentID != d.ID || rec.SchemaVersion != 1 || rec.RunID != nil || rec.UpdatedAt.IsZero() {
		t.Errorf("record row = %+v", rec)
	}
	var got map[string]any
	if err := json.Unmarshal(rec.Record, &got); err != nil || got["schema_version"] != float64(1) {
		t.Errorf("record = %s (%v)", rec.Record, err)
	}
}

func TestStoragePathsListsEveryDocumentsFile(t *testing.T) {
	s := store(t)
	org := mustOrg(t, s)
	u := mustUser(t, s, "a@hospital.test")
	p := mustPatient(t, s, org, u.ID, "SH-1")
	mustDocument(t, s, p, u, "/uploads/one.pdf")
	mustDocument(t, s, p, u, "/uploads/two.pdf")
	got, err := s.StoragePaths(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(got)
	if strings.Join(got, ",") != "/uploads/one.pdf,/uploads/two.pdf" {
		t.Errorf("paths = %v", got)
	}
}
