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

// ----- Login and health -----

func TestLoginReturnsAWorkingToken(t *testing.T) {
	e := newTestEnv(t)

	rec := e.do(http.MethodPost, "/login", "",
		strings.NewReader(`{"phone_number": "+910000000001", "full_name": "Asha Rao"}`), "application/json")

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", rec.Code, rec.Body)
	}
	resp := decodeJSON[loginResponse](t, rec)
	if resp.User.PhoneNumber != "+910000000001" || resp.User.FullName == nil || *resp.User.FullName != "Asha Rao" {
		t.Errorf("user = %+v", resp.User)
	}
	list := e.do(http.MethodGet, "/documents", "Bearer "+resp.Token, nil, "")
	if list.Code != http.StatusOK {
		t.Errorf("the issued token was refused: %d", list.Code)
	}
}

func TestLoginRejectsBadRequests(t *testing.T) {
	tests := []struct {
		name, method, body string
		wantCode           int
	}{
		{"not JSON", http.MethodPost, "phone=1", http.StatusBadRequest},
		{"no phone number", http.MethodPost, `{}`, http.StatusBadRequest},
		{"blank phone number", http.MethodPost, `{"phone_number": "   "}`, http.StatusBadRequest},
		{"GET", http.MethodGet, "", http.StatusMethodNotAllowed},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newTestEnv(t)

			rec := e.do(tt.method, "/login", "", strings.NewReader(tt.body), "application/json")

			if rec.Code != tt.wantCode {
				t.Errorf("status = %d, want %d", rec.Code, tt.wantCode)
			}
			if len(e.store.users) != 0 {
				t.Error("a user was created")
			}
		})
	}
}

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

func TestProtectedRoutesNeedAToken(t *testing.T) {
	e := newTestEnv(t)
	routes := []struct{ method, path string }{
		{http.MethodGet, "/documents"},
		{http.MethodPost, "/documents"},
		{http.MethodDelete, "/documents/" + unknownID},
		{http.MethodGet, "/documents/" + unknownID + "/insight"},
		{http.MethodPost, "/documents/" + unknownID + "/insight"},
		{http.MethodGet, "/user/insights"},
		{http.MethodPost, "/user/insights"},
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
	_, auth := e.login("+910000000001")

	tests := []struct {
		method, path string
		wantCode     int
	}{
		{http.MethodGet, "/documents/not-a-uuid", http.StatusBadRequest},
		{http.MethodGet, "/documents/not-a-uuid/insight", http.StatusBadRequest},
		{http.MethodGet, "/documents/", http.StatusNotFound},
		{http.MethodGet, "/documents/" + unknownID + "/other", http.StatusNotFound},
		{http.MethodGet, "/documents/" + unknownID + "/insight/extra", http.StatusNotFound},
		{http.MethodGet, "/documents/" + unknownID, http.StatusMethodNotAllowed},
		{http.MethodPut, "/documents/" + unknownID + "/insight", http.StatusMethodNotAllowed},
		{http.MethodPut, "/documents", http.StatusMethodNotAllowed},
		{http.MethodDelete, "/user/insights", http.StatusMethodNotAllowed},
	}
	for _, tt := range tests {
		if rec := e.do(tt.method, tt.path, auth, nil, ""); rec.Code != tt.wantCode {
			t.Errorf("%s %s = %d, want %d", tt.method, tt.path, rec.Code, tt.wantCode)
		}
	}
}

// ----- Listing and deleting documents -----

func TestListShowsOnlyTheCallersDocuments(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("+910000000001")
	other, _ := e.login("+910000000002")
	mine := e.store.addDocument(me, "a.pdf")
	e.store.addDocument(other, "b.pdf")
	e.store.insights[mine] = &db.Insight{DocumentID: mine, Status: "completed"}

	rec := e.do(http.MethodGet, "/documents", auth, nil, "")

	docs := decodeJSON[[]db.Document](t, rec)
	if len(docs) != 1 || docs[0].ID != mine {
		t.Fatalf("listed %+v, want only %s", docs, mine)
	}
	if docs[0].InsightStatus != "completed" {
		t.Errorf("insight_status = %q, want completed", docs[0].InsightStatus)
	}
	if strings.Contains(rec.Body.String(), "a.pdf") {
		t.Error("response leaks the storage path")
	}
}

func TestAnEmptyListIsAnEmptyArray(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("+910000000001")

	rec := e.do(http.MethodGet, "/documents", auth, nil, "")

	if got := strings.TrimSpace(rec.Body.String()); got != "[]" {
		t.Errorf("body = %q, want []", got)
	}
}

func TestDeleteRemovesTheDocumentAndItsFile(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("+910000000001")
	path := filepath.Join(e.uploads, "a.pdf")
	if err := os.WriteFile(path, pdfBytes, 0o644); err != nil {
		t.Fatal(err)
	}
	doc := e.store.addDocument(me, path)

	rec := e.do(http.MethodDelete, "/documents/"+doc, auth, nil, "")

	if rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204", rec.Code)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Error("file was not removed")
	}
	if _, ok := e.store.documents[doc]; ok {
		t.Error("document was not deleted")
	}
}

func TestDeleteKeepsAFileAnotherDocumentShares(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("+910000000001")
	path := filepath.Join(e.uploads, "shared.pdf")
	if err := os.WriteFile(path, pdfBytes, 0o644); err != nil {
		t.Fatal(err)
	}
	doc := e.store.addDocument(me, path)
	e.store.addDocument(me, path)

	if rec := e.do(http.MethodDelete, "/documents/"+doc, auth, nil, ""); rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204", rec.Code)
	}
	if _, err := os.Stat(path); err != nil {
		t.Errorf("shared file was removed: %v", err)
	}
}

func TestDeleteOfAnotherUsersDocumentIsNotFound(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("+910000000001")
	other, _ := e.login("+910000000002")
	path := filepath.Join(e.uploads, "theirs.pdf")
	if err := os.WriteFile(path, pdfBytes, 0o644); err != nil {
		t.Fatal(err)
	}
	doc := e.store.addDocument(other, path)

	if rec := e.do(http.MethodDelete, "/documents/"+doc, auth, nil, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", rec.Code)
	}
	if _, ok := e.store.documents[doc]; !ok {
		t.Error("another user's document was deleted")
	}
	if _, err := os.Stat(path); err != nil {
		t.Error("another user's file was removed")
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
		{"completed", &db.Insight{Status: "completed", HTMLInsights: "<h2>Summary</h2>"}, "completed", true, false},
		{"failed", &db.Insight{Status: "failed", ErrorMessage: strPtr("model down")}, "failed", false, true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newTestEnv(t)
			me, auth := e.login("+910000000001")
			doc := e.store.addDocument(me, "a.pdf")
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

func TestInsightRoutesHideOtherUsersDocuments(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("+910000000001")
	other, _ := e.login("+910000000002")
	theirs := e.store.addDocument(other, "b.pdf")
	e.store.insights[theirs] = &db.Insight{DocumentID: theirs, Status: "completed", HTMLInsights: "<p>private</p>"}

	for _, method := range []string{http.MethodGet, http.MethodPost} {
		rec := e.do(method, "/documents/"+theirs+"/insight", auth, nil, "")
		if rec.Code != http.StatusNotFound {
			t.Errorf("%s = %d, want 404", method, rec.Code)
		}
		if strings.Contains(rec.Body.String(), "private") {
			t.Errorf("%s leaked another user's insight", method)
		}
	}
	if len(e.ai.received()) != 0 {
		t.Error("processing was requested for another user's document")
	}
}

func TestGenerateInsightForwardsToTheAIService(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("+910000000001")
	doc := e.store.addDocument(me, "a.pdf")

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

func TestGenerateInsightReportsAnUnavailableAIService(t *testing.T) {
	for name, breakAI := range map[string]func(*fakeAI){
		"error status": func(ai *fakeAI) { ai.status = http.StatusInternalServerError },
		"unreachable":  func(ai *fakeAI) { ai.srv.Close() },
	} {
		t.Run(name, func(t *testing.T) {
			e := newTestEnv(t)
			me, auth := e.login("+910000000001")
			doc := e.store.addDocument(me, "a.pdf")
			breakAI(e.ai)

			rec := e.do(http.MethodPost, "/documents/"+doc+"/insight", auth, nil, "")

			if rec.Code != http.StatusBadGateway {
				t.Errorf("status = %d, want 502", rec.Code)
			}
		})
	}
}

// ----- Patient summary -----

func TestGetPatientSummaryReportsItsStatus(t *testing.T) {
	tests := []struct {
		name     string
		summary  *db.PatientSummary
		want     string
		wantHTML string
		wantErr  string
	}{
		{"never requested", nil, "none", "", ""},
		{"summary from before statuses existed", &db.PatientSummary{HTML: strPtr("<p>s</p>")}, "completed", "<p>s</p>", ""},
		{"completed", &db.PatientSummary{HTML: strPtr("<p>s</p>"), Status: strPtr("completed")}, "completed", "<p>s</p>", ""},
		{"regenerating keeps the last summary", &db.PatientSummary{HTML: strPtr("<p>old</p>"), Status: strPtr("processing")}, "processing", "<p>old</p>", ""},
		{"failed", &db.PatientSummary{Status: strPtr("failed"), Error: strPtr("no documents")}, "failed", "", "no documents"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newTestEnv(t)
			me, auth := e.login("+910000000001")
			if tt.summary != nil {
				e.store.summaries[me] = tt.summary
			}

			resp := decodeJSON[patientInsightResponse](t, e.do(http.MethodGet, "/user/insights", auth, nil, ""))

			if resp.Status != tt.want {
				t.Errorf("status = %q, want %q", resp.Status, tt.want)
			}
			if got := deref(resp.InsightsHTML); got != tt.wantHTML {
				t.Errorf("insights_html = %q, want %q", got, tt.wantHTML)
			}
			if got := deref(resp.ErrorMessage); got != tt.wantErr {
				t.Errorf("error_message = %q, want %q", got, tt.wantErr)
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

func TestPatientSummaryIsRequestedForTheCallerOnly(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("+910000000001")

	// A user_id in the body must not change whose summary is generated.
	rec := e.do(http.MethodPost, "/user/insights", auth,
		strings.NewReader(`{"user_id": "someone-else"}`), "application/json")

	if rec.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want 202", rec.Code)
	}
	calls := e.ai.received()
	if len(calls) != 1 || calls[0].Path != "/internal/generate-user-insights" ||
		calls[0].Token != testInternalToken || calls[0].Body["user_id"] != me {
		t.Errorf("AI calls = %+v, want one for %s", calls, me)
	}
}

func TestPatientSummaryReportsAnUnavailableAIService(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("+910000000001")
	e.ai.status = http.StatusServiceUnavailable

	if rec := e.do(http.MethodPost, "/user/insights", auth, nil, ""); rec.Code != http.StatusBadGateway {
		t.Errorf("status = %d, want 502", rec.Code)
	}
}

func TestDatabaseErrorsAreInternalErrorsWithoutDetails(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("+910000000001")
	doc := e.store.addDocument(me, "a.pdf")
	e.store.failWith = os.ErrPermission

	for _, r := range []struct{ method, path string }{
		{http.MethodGet, "/documents"},
		{http.MethodDelete, "/documents/" + doc},
		{http.MethodGet, "/documents/" + doc + "/insight"},
		{http.MethodPost, "/documents/" + doc + "/insight"},
		{http.MethodGet, "/user/insights"},
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
