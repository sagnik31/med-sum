package httpapi

import (
	"encoding/json"
	"net/http"
	"slices"
	"strings"
	"testing"

	"backend/internal/db"
)

// testRecord with one correction of the flagged result's value.
var correctedRecord = strings.Replace(testRecord,
	`"test_name": "TSH", "value_text": "4"},`,
	`"test_name": "TSH", "value_text": "4"},
     "corrections": [{"field": "value_text", "original": "4", "corrected": "40", "clinician": "Dr. B", "at": "2026-11-12T10:00:00Z"}],`, 1)

func recordViewEnv(t *testing.T, record string) (*testEnv, string, string) {
	t.Helper()
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, "a.pdf")
	addRecord(e.store, doc, record)
	e.store.insights[doc] = &db.Insight{DocumentID: doc, Status: "needs_review"}
	return e, auth, doc
}

func TestARecordWithoutCorrectionsIsItsOwnReviewedView(t *testing.T) {
	e, auth, doc := recordViewEnv(t, testRecord)

	rec := e.do(http.MethodGet, "/documents/"+doc+"/record?view=reviewed", auth, nil, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	resp := decodeJSON[recordResponse](t, rec)
	if string(resp.Reviewed) != string(resp.Record) {
		t.Errorf("reviewed = %s, want the record itself", resp.Reviewed)
	}
	if calls := e.ai.received(); len(calls) != 0 {
		t.Errorf("the AI service was asked %d times for a record with nothing to apply", len(calls))
	}
}

func TestACorrectedRecordsReviewedViewComesFromTheAIService(t *testing.T) {
	e, auth, doc := recordViewEnv(t, correctedRecord)
	e.ai.respond = func(path string, _ []byte) (int, []byte) {
		if path != "/internal/reviewed-view" {
			return 0, nil
		}
		return http.StatusOK, []byte(`{"record": {"schema_version": 1, "view": true}}`)
	}

	rec := e.do(http.MethodGet, "/documents/"+doc+"/record?view=reviewed", auth, nil, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	resp := decodeJSON[recordResponse](t, rec)
	if string(resp.Reviewed) != `{"schema_version":1,"view":true}` {
		t.Errorf("reviewed = %s, want the AI service's view", resp.Reviewed)
	}
	if !strings.Contains(string(resp.Record), `"corrected":"40"`) {
		t.Error("the stored record should come back unchanged beside the view")
	}
	calls := e.ai.received()
	if len(calls) != 1 || calls[0].Path != "/internal/reviewed-view" || calls[0].Token == "" {
		t.Fatalf("AI calls = %+v", calls)
	}
	var sent struct {
		Record json.RawMessage `json:"record"`
	}
	if err := json.Unmarshal(calls[0].Raw, &sent); err != nil || !strings.Contains(string(sent.Record), `"corrected"`) {
		t.Errorf("sent %s, want the stored record", calls[0].Raw)
	}
	if !slices.Contains(e.store.auditActions(), actRecordViewed) {
		t.Error("the read should be audited")
	}
}

func TestWithoutTheViewTheAIServiceIsNotAsked(t *testing.T) {
	e, auth, doc := recordViewEnv(t, correctedRecord)

	rec := e.do(http.MethodGet, "/documents/"+doc+"/record", auth, nil, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if strings.Contains(rec.Body.String(), `"reviewed"`) {
		t.Error("reviewed should be sent only when asked for")
	}
	if calls := e.ai.received(); len(calls) != 0 {
		t.Errorf("AI calls = %d, want 0", len(calls))
	}
}

func TestAReviewedViewThatCantBeComputedSendsNothingAndIsNotAudited(t *testing.T) {
	e, auth, doc := recordViewEnv(t, correctedRecord)
	e.ai.status = http.StatusInternalServerError

	rec := e.do(http.MethodGet, "/documents/"+doc+"/record?view=reviewed", auth, nil, "")
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", rec.Code)
	}
	if strings.Contains(rec.Body.String(), "TSH") || strings.Contains(rec.Body.String(), "40") {
		t.Errorf("body %q leaks record content", rec.Body)
	}
	if slices.Contains(e.store.auditActions(), actRecordViewed) {
		t.Error("no audit row for a read that returned nothing")
	}
}

func TestHasCorrectionsLooksAtEveryHolder(t *testing.T) {
	cases := map[string]bool{
		testRecord:                                                 false,
		correctedRecord:                                            true,
		`{"document": {"corrections": [{}]}}`:                      true,
		`{"patient": {"corrections": [{}]}}`:                       true,
		`{"patient": null, "document": {"corrections": []}}`:       false,
		`{"imaging_findings": [{"id": "i", "corrections": [{}]}]}`: true,
	}
	for raw, want := range cases {
		got, err := hasCorrections(json.RawMessage(raw))
		if err != nil || got != want {
			t.Errorf("hasCorrections(%.60s) = %v, %v; want %v", raw, got, err, want)
		}
	}
}
