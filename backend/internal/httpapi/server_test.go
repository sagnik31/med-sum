package httpapi

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"backend/internal/db"
)

const unknownID = "00000000-0000-4000-8000-999999999999"

func strPtr(s string) *string { return &s }

// ----- Health -----

func TestHealth(t *testing.T) {
	e := newTestEnv(t)

	if rec := e.do(http.MethodGet, "/healthz", "", nil, ""); rec.Code != http.StatusOK || rec.Body.String() != "ok" {
		t.Errorf("GET /healthz = %d %q", rec.Code, rec.Body)
	}
	if rec := e.do(http.MethodPost, "/healthz", "", nil, ""); rec.Code != http.StatusMethodNotAllowed {
		t.Errorf("POST /healthz = %d, want 405", rec.Code)
	}
}

// ----- Routing -----

func TestProtectedRoutesNeedASession(t *testing.T) {
	e := newTestEnv(t)
	routes := []struct{ method, path string }{
		{http.MethodGet, "/auth/me"},
		{http.MethodPost, "/auth/password"},
		{http.MethodGet, "/patients"},
		{http.MethodPost, "/patients"},
		{http.MethodGet, "/patients/" + unknownID},
		{http.MethodPatch, "/patients/" + unknownID},
		{http.MethodGet, "/patients/" + unknownID + "/documents"},
		{http.MethodPost, "/patients/" + unknownID + "/documents"},
		{http.MethodGet, "/patients/" + unknownID + "/summary"},
		{http.MethodPost, "/patients/" + unknownID + "/summary"},
		{http.MethodGet, "/documents/" + unknownID},
		{http.MethodDelete, "/documents/" + unknownID},
		{http.MethodGet, "/documents/" + unknownID + "/insight"},
		{http.MethodPost, "/documents/" + unknownID + "/insight"},
		{http.MethodGet, "/documents/" + unknownID + "/record"},
	}
	for _, r := range routes {
		if rec := e.do(r.method, r.path, "", nil, ""); rec.Code != http.StatusUnauthorized {
			t.Errorf("%s %s = %d, want 401", r.method, r.path, rec.Code)
		}
	}
	if len(e.ai.received()) != 0 {
		t.Error("an unauthenticated request reached the AI service")
	}
}

func TestDocumentRouting(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")

	tests := []struct {
		method, path string
		wantCode     int
	}{
		{http.MethodGet, "/documents/not-a-uuid", http.StatusBadRequest},
		{http.MethodGet, "/documents/not-a-uuid/insight", http.StatusBadRequest},
		{http.MethodGet, "/documents/", http.StatusNotFound},
		{http.MethodGet, "/documents/" + unknownID, http.StatusNotFound},
		{http.MethodGet, "/documents/" + unknownID + "/other", http.StatusNotFound},
		{http.MethodGet, "/documents/" + unknownID + "/insight/extra", http.StatusNotFound},
		{http.MethodPut, "/documents/" + unknownID, http.StatusMethodNotAllowed},
		{http.MethodPut, "/documents/" + unknownID + "/insight", http.StatusMethodNotAllowed},
		// The caller-owned summary route of before patient records is gone.
		{http.MethodGet, "/user/insights", http.StatusNotFound},
	}
	for _, tt := range tests {
		if rec := e.do(tt.method, tt.path, auth, nil, ""); rec.Code != tt.wantCode {
			t.Errorf("%s %s = %d, want %d", tt.method, tt.path, rec.Code, tt.wantCode)
		}
	}
}

// ----- A single document -----

func TestGetDocumentReturnsItWithoutServerSideFields(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	p := e.store.addPatient("org-1", "SH-1")
	doc := e.store.addDocument(p, me, "secret/path/a.pdf")

	rec := e.do(http.MethodGet, "/documents/"+doc, auth, nil, "")

	got := decodeJSON[db.Document](t, rec)
	if got.ID != doc || got.PatientID != p.ID || got.UploadedBy == nil || *got.UploadedBy != me ||
		got.UploadedByName == nil || *got.UploadedByName != "Dr me@hospital.test" {
		t.Errorf("document = %+v", got)
	}
	if strings.Contains(rec.Body.String(), "secret/path") {
		t.Error("response leaks the storage path")
	}
}

// Every clinician of the organisation sees every patient's documents.
func TestAColleagueSeesTheDocument(t *testing.T) {
	e := newTestEnv(t)
	me, _ := e.login("me@hospital.test")
	_, colleague := e.login("colleague@hospital.test")
	doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, "a.pdf")

	for _, path := range []string{"/documents/" + doc, "/documents/" + doc + "/insight"} {
		if rec := e.do(http.MethodGet, path, colleague, nil, ""); rec.Code != http.StatusOK {
			t.Errorf("GET %s by a colleague = %d, want 200", path, rec.Code)
		}
	}
}

// A document of another organisation answers 404 on every route, as if
// it didn't exist, and nothing reaches the AI service.
func TestAnotherOrganisationsDocumentIsNotFoundOnEveryRoute(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	them := e.store.addUserIn("org-2", "them@other.test", db.StatusActive)
	theirs := e.store.addDocument(e.store.addPatient("org-2", "SH-1"), them.ID, "b.pdf")
	e.store.insights[theirs] = &db.Insight{DocumentID: theirs, Status: "verified", HTMLInsights: "<p>private</p>"}
	e.store.records[theirs] = &db.DocumentRecord{DocumentID: theirs, SchemaVersion: 1, Record: []byte(`{"private": true}`)}

	for _, r := range []struct{ method, path string }{
		{http.MethodGet, "/documents/" + theirs},
		{http.MethodDelete, "/documents/" + theirs},
		{http.MethodGet, "/documents/" + theirs + "/insight"},
		{http.MethodPost, "/documents/" + theirs + "/insight"},
		{http.MethodGet, "/documents/" + theirs + "/record"},
		{http.MethodPost, "/documents/" + theirs + "/reprocess"},
	} {
		rec := e.do(r.method, r.path, auth, nil, "")
		if rec.Code != http.StatusNotFound {
			t.Errorf("%s %s = %d, want 404", r.method, r.path, rec.Code)
		}
		if strings.Contains(rec.Body.String(), "private") {
			t.Errorf("%s %s leaked another organisation's data", r.method, r.path)
		}
	}
	if len(e.ai.received()) != 0 {
		t.Error("processing was requested for another organisation's document")
	}
	if _, gone := e.store.deleted[theirs]; gone {
		t.Error("another organisation's document was deleted")
	}
}

// ----- Deleting a document -----

func TestDeleteIsSoftAndKeepsTheFile(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	path := filepath.Join(e.uploads, "a.pdf")
	if err := os.WriteFile(path, pdfBytes, 0o644); err != nil {
		t.Fatal(err)
	}
	p := e.store.addPatient("org-1", "SH-1")
	doc := e.store.addDocument(p, me, path)

	rec := e.do(http.MethodDelete, "/documents/"+doc, auth, nil, "")

	if rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204", rec.Code)
	}
	if e.store.deleted[doc] != me {
		t.Errorf("deleted by %q, want %s", e.store.deleted[doc], me)
	}
	if _, err := os.Stat(path); err != nil {
		t.Errorf("the file of a soft-deleted document was removed: %v", err)
	}
	for _, path := range []string{"/documents/" + doc, "/documents/" + doc + "/insight"} {
		if rec := e.do(http.MethodGet, path, auth, nil, ""); rec.Code != http.StatusNotFound {
			t.Errorf("GET %s after delete = %d, want 404", path, rec.Code)
		}
	}
	if docs := decodeJSON[[]db.Document](t, e.do(http.MethodGet, "/patients/"+p.ID+"/documents", auth, nil, "")); len(docs) != 0 {
		t.Errorf("a deleted document is still listed: %+v", docs)
	}
	if rec := e.do(http.MethodDelete, "/documents/"+doc, auth, nil, ""); rec.Code != http.StatusNotFound {
		t.Errorf("second delete = %d, want 404", rec.Code)
	}
	if len(e.ai.received()) != 0 {
		t.Error("a summary was rebuilt for a patient who never had one")
	}
}

func TestOnlyTheUploaderCanDeleteADocument(t *testing.T) {
	e := newTestEnv(t)
	me, _ := e.login("me@hospital.test")
	_, colleague := e.login("colleague@hospital.test")
	doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, "a.pdf")

	rec := e.do(http.MethodDelete, "/documents/"+doc, colleague, nil, "")

	if rec.Code != http.StatusForbidden || !strings.Contains(rec.Body.String(), "uploaded") {
		t.Errorf("delete by a colleague = %d %q, want 403", rec.Code, rec.Body)
	}
	if _, gone := e.store.deleted[doc]; gone {
		t.Error("a colleague deleted the document")
	}
}

func TestADocumentWithNoUploaderCantBeDeleted(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), "", "a.pdf")

	if rec := e.do(http.MethodDelete, "/documents/"+doc, auth, nil, ""); rec.Code != http.StatusForbidden {
		t.Errorf("status = %d, want 403", rec.Code)
	}
}

func TestDeleteRebuildsThePatientsSummary(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	p := e.store.addPatient("org-1", "SH-1")
	doc := e.store.addDocument(p, me, "a.pdf")
	e.store.summaries[p.ID] = &db.PatientSummary{HTML: strPtr("<p>s</p>"), Status: strPtr("completed")}

	if rec := e.do(http.MethodDelete, "/documents/"+doc, auth, nil, ""); rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204", rec.Code)
	}
	calls := e.ai.received()
	if len(calls) != 1 || calls[0].Path != "/internal/generate-patient-summary" || calls[0].Body["patient_id"] != p.ID {
		t.Errorf("AI calls = %+v, want one summary rebuild for %s", calls, p.ID)
	}
}

func TestDeleteStandsWhenTheSummaryCantBeRebuilt(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	p := e.store.addPatient("org-1", "SH-1")
	doc := e.store.addDocument(p, me, "a.pdf")
	e.store.summaries[p.ID] = &db.PatientSummary{Status: strPtr("completed")}
	e.ai.status = http.StatusServiceUnavailable

	if rec := e.do(http.MethodDelete, "/documents/"+doc, auth, nil, ""); rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204", rec.Code)
	}
}

// ----- Document insight -----

func TestGetInsightReportsTheJobStatus(t *testing.T) {
	tests := []struct {
		name     string
		insight  *db.Insight
		want     string
		wantHTML bool
		wantErr  bool
	}{
		{"no job yet", nil, "not_started", false, false},
		{"processing", &db.Insight{Status: "processing", HTMLInsights: "<p>old</p>"}, "processing", false, false},
		{"checks passed", &db.Insight{Status: "checks_passed", HTMLInsights: "<h2>Summary</h2>"}, "checks_passed", true, false},
		{"verified", &db.Insight{Status: "verified", HTMLInsights: "<h2>Summary</h2>"}, "verified", true, false},
		{"needs review", &db.Insight{Status: "needs_review", HTMLInsights: "<h2>Summary</h2>"}, "needs_review", true, false},
		{"failed", &db.Insight{Status: "failed", ErrorMessage: strPtr("model down")}, "failed", false, true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newTestEnv(t)
			me, auth := e.login("me@hospital.test")
			doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, "a.pdf")
			if tt.insight != nil {
				tt.insight.DocumentID = doc
				e.store.insights[doc] = tt.insight
			}

			rec := e.do(http.MethodGet, "/documents/"+doc+"/insight", auth, nil, "")

			resp := decodeJSON[insightResponse](t, rec)
			if resp.Status != tt.want || resp.DocumentID != doc {
				t.Errorf("response = %+v, want status %s", resp, tt.want)
			}
			if (resp.InsightsHTML != nil) != tt.wantHTML {
				t.Errorf("insights_html present = %v, want %v", resp.InsightsHTML != nil, tt.wantHTML)
			}
			if (resp.ErrorMessage != nil) != tt.wantErr {
				t.Errorf("error_message present = %v, want %v", resp.ErrorMessage != nil, tt.wantErr)
			}
		})
	}
}

func TestGenerateInsightForwardsToTheAIService(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, "a.pdf")

	rec := e.do(http.MethodPost, "/documents/"+doc+"/insight", auth, nil, "")

	if rec.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want 202", rec.Code)
	}
	calls := e.ai.received()
	if len(calls) != 1 || calls[0].Path != "/internal/generate-insights" ||
		calls[0].Token != testInternalToken || calls[0].Body["document_id"] != doc {
		t.Errorf("AI calls = %+v", calls)
	}
}

func TestReprocessForwardsToTheAIService(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, "a.pdf")
	e.store.insights[doc] = &db.Insight{DocumentID: doc, Status: "verified"}

	rec := e.do(http.MethodPost, "/documents/"+doc+"/reprocess", auth, nil, "")

	if rec.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want 202", rec.Code)
	}
	calls := e.ai.received()
	if len(calls) != 1 || calls[0].Path != "/internal/reprocess-document" ||
		calls[0].Token != testInternalToken || calls[0].Body["document_id"] != doc {
		t.Errorf("AI calls = %+v", calls)
	}
}

func TestReprocessIsPostOnly(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, "a.pdf")

	rec := e.do(http.MethodGet, "/documents/"+doc+"/reprocess", auth, nil, "")

	if rec.Code != http.StatusMethodNotAllowed {
		t.Errorf("status = %d, want 405", rec.Code)
	}
}

func TestReprocessReportsAnUnavailableAIService(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, "a.pdf")
	e.ai.status = http.StatusInternalServerError

	rec := e.do(http.MethodPost, "/documents/"+doc+"/reprocess", auth, nil, "")

	if rec.Code != http.StatusBadGateway {
		t.Errorf("status = %d, want 502", rec.Code)
	}
}

func TestGenerateInsightReportsAnUnavailableAIService(t *testing.T) {
	for name, breakAI := range map[string]func(*fakeAI){
		"error status": func(ai *fakeAI) { ai.status = http.StatusInternalServerError },
		"unreachable":  func(ai *fakeAI) { ai.srv.Close() },
	} {
		t.Run(name, func(t *testing.T) {
			e := newTestEnv(t)
			me, auth := e.login("me@hospital.test")
			doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, "a.pdf")
			breakAI(e.ai)

			rec := e.do(http.MethodPost, "/documents/"+doc+"/insight", auth, nil, "")

			if rec.Code != http.StatusBadGateway {
				t.Errorf("status = %d, want 502", rec.Code)
			}
		})
	}
}

func deref(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

func TestDatabaseErrorsAreInternalErrorsWithoutDetails(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	p := e.store.addPatient("org-1", "SH-1")
	doc := e.store.addDocument(p, me, "a.pdf")
	e.store.failWith = os.ErrPermission

	for _, r := range []struct{ method, path string }{
		{http.MethodGet, "/patients"},
		{http.MethodGet, "/patients/" + p.ID},
		{http.MethodGet, "/patients/" + p.ID + "/documents"},
		{http.MethodGet, "/patients/" + p.ID + "/summary"},
		{http.MethodGet, "/documents/" + doc},
		{http.MethodDelete, "/documents/" + doc},
		{http.MethodGet, "/documents/" + doc + "/insight"},
		{http.MethodPost, "/documents/" + doc + "/insight"},
	} {
		rec := e.do(r.method, r.path, auth, nil, "")
		if rec.Code != http.StatusInternalServerError {
			t.Errorf("%s %s = %d, want 500", r.method, r.path, rec.Code)
		}
		if strings.Contains(rec.Body.String(), "permission") {
			t.Errorf("%s %s leaks the database error", r.method, r.path)
		}
	}
}
