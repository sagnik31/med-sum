package httpapi

import (
	"net/http"
	"strings"
	"testing"

	"backend/internal/db"
)

func TestCreatePatient(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")

	rec := e.postJSON("/patients", auth, map[string]any{
		"mrn": " SH-100231 ", "full_name": "  Meera   Iyer ", "date_of_birth": "1967-04-12", "sex": "F",
	})

	if rec.Code != http.StatusCreated {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	p := decodeJSON[db.Patient](t, rec)
	if p.ID == "" || p.MRN != "SH-100231" || p.FullName != "Meera Iyer" || deref(p.DateOfBirth) != "1967-04-12" ||
		deref(p.Sex) != "F" || p.AgeYears != nil || deref(p.CreatedBy) != me {
		t.Errorf("patient = %+v", p)
	}
	if stored := e.store.patients[p.ID]; stored.OrgID != "org-1" {
		t.Errorf("stored in %q, want the caller's organisation", stored.OrgID)
	}
	if strings.Contains(rec.Body.String(), "org_id") {
		t.Error("the response carries the organisation id")
	}
}

func TestCreatePatientWithAnAgeOnly(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")

	rec := e.postJSON("/patients", auth, map[string]any{"mrn": "SH-2", "full_name": "Kabir Sethi", "age_years": 46})

	p := decodeJSON[db.Patient](t, rec)
	if rec.Code != http.StatusCreated || p.AgeYears == nil || *p.AgeYears != 46 || p.DateOfBirth != nil {
		t.Errorf("%d %+v", rec.Code, p)
	}
}

func TestCreatePatientRejectsBadInput(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	for name, body := range map[string]map[string]any{
		"no mrn":           {"full_name": "A"},
		"blank mrn":        {"mrn": "  ", "full_name": "A"},
		"no name":          {"mrn": "SH-1"},
		"long mrn":         {"mrn": strings.Repeat("1", 65), "full_name": "A"},
		"bad date":         {"mrn": "SH-1", "full_name": "A", "date_of_birth": "12/04/1967"},
		"future birth":     {"mrn": "SH-1", "full_name": "A", "date_of_birth": "2999-01-01"},
		"date and age":     {"mrn": "SH-1", "full_name": "A", "date_of_birth": "1967-04-12", "age_years": 50},
		"negative age":     {"mrn": "SH-1", "full_name": "A", "age_years": -1},
		"unknown sex":      {"mrn": "SH-1", "full_name": "A", "sex": "female"},
		"unknown field":    {"mrn": "SH-1", "full_name": "A", "org_id": "org-2"},
		"mrn not a string": {"mrn": 5, "full_name": "A"},
	} {
		if rec := e.postJSON("/patients", auth, body); rec.Code != http.StatusBadRequest {
			t.Errorf("%s: status = %d, want 400", name, rec.Code)
		}
	}
	if len(e.store.patients) != 0 {
		t.Errorf("%d patients stored", len(e.store.patients))
	}
}

func TestAnMRNIsUniqueInTheOrganisation(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	e.store.addPatient("org-1", "SH-1")
	e.store.addPatient("org-2", "SH-2")

	if rec := e.postJSON("/patients", auth, map[string]any{"mrn": "SH-1", "full_name": "A"}); rec.Code != http.StatusConflict {
		t.Errorf("same MRN: status = %d, want 409", rec.Code)
	}
	if rec := e.postJSON("/patients", auth, map[string]any{"mrn": "SH-2", "full_name": "A"}); rec.Code != http.StatusCreated {
		t.Errorf("another organisation's MRN: status = %d, want 201", rec.Code)
	}
}

func TestListPatientsSearchesTheOrganisationOnly(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	mine := e.store.addPatient("org-1", "SH-100231")
	e.store.addPatient("org-2", "SH-100232")

	all := decodeJSON[[]db.Patient](t, e.do(http.MethodGet, "/patients", auth, nil, ""))
	if len(all) != 1 || all[0].ID != mine.ID {
		t.Errorf("listed %+v, want only the organisation's patient", all)
	}
	found := decodeJSON[[]db.Patient](t, e.do(http.MethodGet, "/patients?q=sh-1002", auth, nil, ""))
	if len(found) != 1 || found[0].ID != mine.ID {
		t.Errorf("search by MRN = %+v", found)
	}
	none := e.do(http.MethodGet, "/patients?q=nobody", auth, nil, "")
	if got := strings.TrimSpace(none.Body.String()); got != "[]" {
		t.Errorf("no match = %q, want []", got)
	}
	if rec := e.do(http.MethodGet, "/patients?q="+strings.Repeat("a", 101), auth, nil, ""); rec.Code != http.StatusBadRequest {
		t.Errorf("long search: status = %d, want 400", rec.Code)
	}
}

func TestUpdatePatientChangesOnlyTheGivenFields(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	p := e.store.addPatient("org-1", "SH-1")
	e.store.patients[p.ID].Sex = strPtr("F")
	e.store.patients[p.ID].DateOfBirth = strPtr("1967-04-12")

	rec := e.sendJSON(http.MethodPatch, "/patients/"+p.ID, auth, map[string]any{"full_name": "Meera Iyer"})
	got := decodeJSON[db.Patient](t, rec)
	if rec.Code != http.StatusOK || got.FullName != "Meera Iyer" || got.MRN != "SH-1" || deref(got.Sex) != "F" ||
		deref(got.DateOfBirth) != "1967-04-12" {
		t.Errorf("%d %+v", rec.Code, got)
	}

	// Switching to an age needs the date cleared in the same request.
	if rec := e.sendJSON(http.MethodPatch, "/patients/"+p.ID, auth, map[string]any{"age_years": 58}); rec.Code != http.StatusBadRequest {
		t.Errorf("age beside a date: status = %d, want 400", rec.Code)
	}
	rec = e.sendJSON(http.MethodPatch, "/patients/"+p.ID, auth, map[string]any{"age_years": 58, "date_of_birth": nil})
	got = decodeJSON[db.Patient](t, rec)
	if rec.Code != http.StatusOK || got.DateOfBirth != nil || got.AgeYears == nil || *got.AgeYears != 58 {
		t.Errorf("%d %+v", rec.Code, got)
	}
	if rec := e.sendJSON(http.MethodPatch, "/patients/"+p.ID, auth, map[string]any{"mrn": nil}); rec.Code != http.StatusBadRequest {
		t.Errorf("clearing the MRN: status = %d, want 400", rec.Code)
	}
}

func TestUpdatePatientToATakenMRNConflicts(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	p := e.store.addPatient("org-1", "SH-1")
	e.store.addPatient("org-1", "SH-2")

	if rec := e.sendJSON(http.MethodPatch, "/patients/"+p.ID, auth, map[string]any{"mrn": "SH-2"}); rec.Code != http.StatusConflict {
		t.Errorf("status = %d, want 409", rec.Code)
	}
}

// A patient of another organisation answers 404 on every patient route,
// and nothing reaches the AI service or the store.
func TestAnotherOrganisationsPatientIsNotFoundOnEveryRoute(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	theirs := e.store.addPatient("org-2", "SH-1")
	e.store.summaries[theirs.ID] = &db.PatientSummary{HTML: strPtr("<p>private</p>"), Status: strPtr("completed")}
	body, ct := multipartFile(t, "file", "cbc.pdf", pdfBytes)

	for _, r := range []struct{ method, path string }{
		{http.MethodGet, "/patients/" + theirs.ID},
		{http.MethodGet, "/patients/" + theirs.ID + "/documents"},
		{http.MethodGet, "/patients/" + theirs.ID + "/summary"},
		{http.MethodPost, "/patients/" + theirs.ID + "/summary"},
	} {
		rec := e.do(r.method, r.path, auth, nil, "")
		if rec.Code != http.StatusNotFound || strings.Contains(rec.Body.String(), "private") {
			t.Errorf("%s %s = %d %q, want 404", r.method, r.path, rec.Code, rec.Body)
		}
	}
	if rec := e.sendJSON(http.MethodPatch, "/patients/"+theirs.ID, auth, map[string]any{"full_name": "X"}); rec.Code != http.StatusNotFound {
		t.Errorf("PATCH = %d, want 404", rec.Code)
	}
	if rec := e.do(http.MethodPost, "/patients/"+theirs.ID+"/documents", auth, body, ct); rec.Code != http.StatusNotFound {
		t.Errorf("upload = %d, want 404", rec.Code)
	}
	if e.store.patients[theirs.ID].FullName == "X" {
		t.Error("another organisation's patient was changed")
	}
	if len(e.store.documents) != 0 || len(e.ai.received()) != 0 {
		t.Error("an upload to another organisation's patient was stored or processed")
	}
}

func TestPatientRouting(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	p := e.store.addPatient("org-1", "SH-1")
	for _, tt := range []struct {
		method, path string
		want         int
	}{
		{http.MethodGet, "/patients/not-a-uuid", http.StatusBadRequest},
		{http.MethodGet, "/patients/", http.StatusNotFound},
		{http.MethodGet, "/patients/" + unknownID, http.StatusNotFound},
		{http.MethodGet, "/patients/" + p.ID + "/other", http.StatusNotFound},
		{http.MethodGet, "/patients/" + p.ID + "/documents/extra", http.StatusNotFound},
		{http.MethodDelete, "/patients/" + p.ID, http.StatusMethodNotAllowed},
		{http.MethodPut, "/patients", http.StatusMethodNotAllowed},
		{http.MethodDelete, "/patients/" + p.ID + "/summary", http.StatusMethodNotAllowed},
	} {
		if rec := e.do(tt.method, tt.path, auth, nil, ""); rec.Code != tt.want {
			t.Errorf("%s %s = %d, want %d", tt.method, tt.path, rec.Code, tt.want)
		}
	}
}

func TestListPatientDocuments(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	p := e.store.addPatient("org-1", "SH-1")
	other := e.store.addPatient("org-1", "SH-2")
	mine := e.store.addDocument(p, me, "a.pdf")
	e.store.addDocument(other, me, "b.pdf")
	e.store.insights[mine] = &db.Insight{DocumentID: mine, Status: "verified"}

	empty := e.do(http.MethodGet, "/patients/"+e.store.addPatient("org-1", "SH-3").ID+"/documents", auth, nil, "")
	if got := strings.TrimSpace(empty.Body.String()); got != "[]" {
		t.Errorf("no documents = %q, want []", got)
	}
	rec := e.do(http.MethodGet, "/patients/"+p.ID+"/documents", auth, nil, "")
	docs := decodeJSON[[]db.Document](t, rec)
	if len(docs) != 1 || docs[0].ID != mine || docs[0].InsightStatus != "verified" {
		t.Errorf("listed %+v, want only %s, verified", docs, mine)
	}
	if strings.Contains(rec.Body.String(), "a.pdf") {
		t.Error("response leaks the storage path")
	}
}

// ----- The patient summary -----

func TestGetPatientSummaryReportsItsStatus(t *testing.T) {
	tests := []struct {
		name     string
		summary  *db.PatientSummary
		want     string
		wantHTML string
		wantErr  string
	}{
		{"never requested", nil, "none", "", ""},
		{"completed", &db.PatientSummary{HTML: strPtr("<p>s</p>"), Status: strPtr("completed")}, "completed", "<p>s</p>", ""},
		{"regenerating keeps the last summary", &db.PatientSummary{HTML: strPtr("<p>old</p>"), Status: strPtr("processing")}, "processing", "<p>old</p>", ""},
		{"failed", &db.PatientSummary{Status: strPtr("failed"), Error: strPtr("no documents")}, "failed", "", "no documents"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newTestEnv(t)
			_, auth := e.login("me@hospital.test")
			p := e.store.addPatient("org-1", "SH-1")
			if tt.summary != nil {
				e.store.summaries[p.ID] = tt.summary
			}

			resp := decodeJSON[patientSummaryResponse](t, e.do(http.MethodGet, "/patients/"+p.ID+"/summary", auth, nil, ""))

			if resp.Status != tt.want || resp.PatientID != p.ID {
				t.Errorf("response = %+v, want status %q", resp, tt.want)
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

func TestPatientSummaryIsRequestedForThatPatient(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	p := e.store.addPatient("org-1", "SH-1")

	// A patient_id in the body must not change whose summary is built.
	rec := e.do(http.MethodPost, "/patients/"+p.ID+"/summary", auth,
		strings.NewReader(`{"patient_id": "someone-else"}`), "application/json")

	if rec.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want 202", rec.Code)
	}
	calls := e.ai.received()
	if len(calls) != 1 || calls[0].Path != "/internal/generate-patient-summary" ||
		calls[0].Token != testInternalToken || calls[0].Body["patient_id"] != p.ID {
		t.Errorf("AI calls = %+v, want one for %s", calls, p.ID)
	}
}

func TestPatientSummaryReportsAnUnavailableAIService(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	p := e.store.addPatient("org-1", "SH-1")
	e.ai.status = http.StatusServiceUnavailable

	if rec := e.do(http.MethodPost, "/patients/"+p.ID+"/summary", auth, nil, ""); rec.Code != http.StatusBadGateway {
		t.Errorf("status = %d, want 502", rec.Code)
	}
}
