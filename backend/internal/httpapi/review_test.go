package httpapi

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"backend/internal/db"
)

var errTest = errors.New("test failure")

// recordTime is the updated_at addRecord gives a record.
var recordTime = time.Date(2026, 11, 12, 10, 0, 0, 0, time.UTC)

// reviewEnv is a clinician with one document of a patient in needs_review,
// its record stored, and a fake AI service that applies reviews by
// answering what the test sets.
type reviewEnv struct {
	*testEnv
	auth    string
	userID  string
	doc     string
	patient *db.Patient
	applied map[string]any // what the fake review-apply answers
}

func newReviewEnv(t *testing.T) *reviewEnv {
	t.Helper()
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	p := e.store.addPatient("org-1", "SH-1")
	doc := e.store.addDocument(p, me, "a.pdf")
	addRecord(e.store, doc, testRecord)
	queued := recordTime
	e.store.insights[doc] = &db.Insight{DocumentID: doc, Status: "needs_review", UpdatedAt: &queued}
	re := &reviewEnv{testEnv: e, auth: auth, userID: me, doc: doc, patient: p,
		applied: map[string]any{
			"record":   json.RawMessage(`{"schema_version": 1, "reviewed": true}`),
			"html":     "<p>reviewed</p>",
			"findings": []any{},
			"signals":  []string{"LC-10"},
			"original": "4",
			"flagged":  1,
		}}
	e.ai.respond = func(path string, _ []byte) (int, []byte) {
		if path != "/internal/review-apply" {
			return 0, nil
		}
		out, _ := json.Marshal(re.applied)
		return http.StatusOK, out
	}
	return re
}

func (re *reviewEnv) review(body map[string]any) *httptest.ResponseRecorder {
	if _, ok := body["record_updated_at"]; !ok {
		body["record_updated_at"] = recordTime
	}
	return re.postJSON("/documents/"+re.doc+"/review", re.auth, body)
}

func correction(field, value string) map[string]any {
	return map[string]any{"action": "correct", "item_id": "obs-2", "field": field, "value": value}
}

func TestACorrectionIsAppliedByTheAIServiceAndStoredWithItsAuditRow(t *testing.T) {
	re := newReviewEnv(t)

	rec := re.review(correction("value_text", "5.2"))
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	resp := decodeJSON[reviewResponse](t, rec)
	if resp.Status != "needs_review" || resp.Flagged != 1 || !resp.RecordUpdatedAt.Equal(re.now()) {
		t.Errorf("response = %+v", resp)
	}

	// The AI service got the stored record and the clinician's name.
	var sent struct {
		Record json.RawMessage `json:"record"`
		Review map[string]any  `json:"review"`
	}
	calls := re.ai.received()
	if err := json.Unmarshal(calls[len(calls)-1].Raw, &sent); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(sent.Record), `"obs-2"`) || sent.Review["field"] != "value_text" ||
		sent.Review["value"] != "5.2" || sent.Review["reviewer"] == "" {
		t.Errorf("sent = %s / %v", sent.Record, sent.Review)
	}

	// Stored: the new record, the page, the trail row.
	if got := string(re.store.records[re.doc].Record); !strings.Contains(got, `"reviewed":true`) {
		t.Errorf("record = %s", got)
	}
	if re.store.insights[re.doc].HTMLInsights != "<p>reviewed</p>" {
		t.Error("the page was not stored")
	}
	if n := len(re.store.reviews); n != 1 {
		t.Fatalf("reviews = %d", n)
	}
	rv := re.store.reviews[0]
	if rv.ItemID != "obs-2" || *rv.Field != "value_text" || *rv.Corrected != "5.2" || *rv.Original != "4" ||
		rv.ReviewerID != re.userID || rv.Action != "correct" || rv.Verified || len(rv.Signals) != 1 {
		t.Errorf("review row = %+v", rv)
	}

	// One audit row, with ids and names, never the values.
	ev := re.store.audit[len(re.store.audit)-1]
	if ev.Action != actFieldCorrected || ev.PatientID == nil || *ev.PatientID != re.patient.ID ||
		ev.Details["item_id"] != "obs-2" || ev.Details["field"] != "value_text" {
		t.Errorf("audit = %+v", ev)
	}
	raw, _ := json.Marshal(ev.Details)
	if strings.Contains(string(raw), "5.2") || strings.Contains(string(raw), `"4"`) {
		t.Errorf("audit details hold a value: %s", raw)
	}
}

func TestAConfirmationThatLeavesNothingFlaggedVerifiesAndRebuildsTheSummary(t *testing.T) {
	re := newReviewEnv(t)
	re.applied["flagged"] = 0
	re.applied["original"] = nil

	rec := re.review(map[string]any{"action": "confirm", "item_id": "obs-2"})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if resp := decodeJSON[reviewResponse](t, rec); resp.Status != "verified" {
		t.Errorf("status = %s", resp.Status)
	}
	if re.store.insights[re.doc].Status != "verified" {
		t.Error("the document was not verified")
	}
	var rebuilt bool
	for _, c := range re.ai.received() {
		if c.Path == "/internal/generate-patient-summary" && c.Body["patient_id"] == re.patient.ID {
			rebuilt = true
		}
	}
	if !rebuilt {
		t.Error("the patient summary was not rebuilt")
	}
	if ev := re.store.audit[len(re.store.audit)-1]; ev.Action != actFieldConfirmed || ev.Details["verified"] != true {
		t.Errorf("audit = %+v", ev)
	}
}

func TestACorrectionThatFailsACheckIsSavedOnlyOnceAcknowledged(t *testing.T) {
	re := newReviewEnv(t)
	re.applied["findings"] = []map[string]any{{"validator": "flag_agreement", "severity": "flag",
		"field": "flag_printed", "message": "flag 'H' is printed, but the value is normal"}}

	rec := re.review(correction("value_text", "3"))
	if rec.Code != http.StatusUnprocessableEntity || !strings.Contains(rec.Body.String(), "flag_agreement") {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if len(re.store.reviews) != 0 || strings.Contains(string(re.store.records[re.doc].Record), "reviewed") {
		t.Fatal("saved before it was acknowledged")
	}

	body := correction("value_text", "3")
	body["accept_findings"] = true
	rec = re.review(body)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if rv := re.store.reviews[0]; len(rv.Findings) != 1 || rv.Findings[0] != "flag_agreement" {
		t.Errorf("findings = %v", rv.Findings)
	}
}

func TestAReviewTheAIServiceCantApplyIsRefusedWithItsReason(t *testing.T) {
	re := newReviewEnv(t)
	re.ai.respond = func(string, []byte) (int, []byte) {
		return http.StatusUnprocessableEntity, []byte(`{"detail": "'citation' can't be corrected"}`)
	}
	rec := re.review(correction("citation", "x"))
	if rec.Code != http.StatusUnprocessableEntity || !strings.Contains(rec.Body.String(), "citation") {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if len(re.store.reviews) != 0 {
		t.Error("saved")
	}
}

func TestAReviewOfAChangedRecordIsAConflict(t *testing.T) {
	re := newReviewEnv(t)
	body := correction("value_text", "5")
	body["record_updated_at"] = recordTime.Add(-time.Minute)
	if rec := re.review(body); rec.Code != http.StatusConflict {
		t.Fatalf("status = %d", rec.Code)
	}
	if n := len(re.ai.received()); n != 0 {
		t.Errorf("the AI service was asked %d times", n)
	}
}

func TestADocumentBeingProcessedCantBeReviewed(t *testing.T) {
	re := newReviewEnv(t)
	re.store.insights[re.doc].Status = "processing"
	if rec := re.review(correction("value_text", "5")); rec.Code != http.StatusConflict {
		t.Fatalf("status = %d", rec.Code)
	}
}

func TestAnotherOrganisationsDocumentCantBeReviewed(t *testing.T) {
	re := newReviewEnv(t)
	theirs := re.store.addDocument(re.store.addPatient("org-2", "OT-1"), re.userID, "b.pdf")
	addRecord(re.store, theirs, testRecord)
	re.store.insights[theirs] = &db.Insight{DocumentID: theirs, Status: "needs_review"}
	body := correction("value_text", "5")
	body["record_updated_at"] = recordTime
	if rec := re.postJSON("/documents/"+theirs+"/review", re.auth, body); rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d", rec.Code)
	}
	if len(re.ai.received()) != 0 || len(re.store.reviews) != 0 {
		t.Error("reviewed another organisation's document")
	}
}

func TestAnAdminCantReview(t *testing.T) {
	re := newReviewEnv(t)
	_, admin := re.loginWith("admin@hospital.test", "admin")
	body := correction("value_text", "5")
	body["record_updated_at"] = recordTime
	if rec := re.postJSON("/documents/"+re.doc+"/review", admin, body); rec.Code != http.StatusForbidden {
		t.Fatalf("status = %d", rec.Code)
	}
	if rec := re.do(http.MethodGet, "/review/queue", admin, nil, ""); rec.Code != http.StatusForbidden {
		t.Fatalf("queue status = %d", rec.Code)
	}
}

func TestAcknowledgedSignalsGoToTheAIServiceAndTheAuditRow(t *testing.T) {
	re := newReviewEnv(t)
	re.applied["signals"] = []string{"LC-13"}

	rec := re.review(map[string]any{"action": "confirm", "item_id": "document", "acknowledge": []string{"LC-13"}})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	var sent struct {
		Review map[string]any `json:"review"`
	}
	calls := re.ai.received()
	if err := json.Unmarshal(calls[len(calls)-1].Raw, &sent); err != nil {
		t.Fatal(err)
	}
	if got, _ := json.Marshal(sent.Review["acknowledge"]); string(got) != `["LC-13"]` {
		t.Errorf("acknowledge sent = %s", got)
	}
	ev := re.store.audit[len(re.store.audit)-1]
	if got, _ := json.Marshal(ev.Details["acknowledged"]); string(got) != `["LC-13"]` {
		t.Errorf("audit details = %+v", ev.Details)
	}

	// Without any, the AI service is sent an empty list, never null.
	re = newReviewEnv(t)
	if rec := re.review(map[string]any{"action": "confirm", "item_id": "obs-2"}); rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	calls = re.ai.received()
	sent.Review = nil
	if err := json.Unmarshal(calls[len(calls)-1].Raw, &sent); err != nil {
		t.Fatal(err)
	}
	if got, _ := json.Marshal(sent.Review["acknowledge"]); string(got) != `[]` {
		t.Errorf("acknowledge sent = %s", got)
	}
}

func TestMalformedReviewsAreRefusedBeforeAnythingIsRead(t *testing.T) {
	cases := map[string]map[string]any{
		"unknown action":        {"action": "approve", "item_id": "obs-2"},
		"confirm with a value":  {"action": "confirm", "item_id": "obs-2", "value": "1"},
		"correct without field": {"action": "correct", "item_id": "obs-2", "value": "1"},
		"blank value":           correction("value_text", "  "),
		"bad item id":           {"action": "confirm", "item_id": "../x"},
		"long value":            correction("value_text", strings.Repeat("9", 501)),
		"bad signal id":         {"action": "confirm", "item_id": "document", "acknowledge": []string{"LC-1x"}},
		"too many signals": {"action": "confirm", "item_id": "document",
			"acknowledge": []string{"LC-01", "LC-02", "LC-03", "LC-04", "LC-05", "LC-06", "LC-07", "LC-08", "LC-09"}},
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			re := newReviewEnv(t)
			if rec := re.review(body); rec.Code != http.StatusBadRequest {
				t.Fatalf("status = %d: %s", rec.Code, rec.Body)
			}
			if len(re.ai.received()) != 0 {
				t.Error("the AI service was asked")
			}
		})
	}
}

func TestAnAuditFailureStoresNoReview(t *testing.T) {
	re := newReviewEnv(t)
	re.store.failAudit = errTest
	if rec := re.review(correction("value_text", "5")); rec.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d", rec.Code)
	}
	if len(re.store.reviews) != 0 {
		t.Error("stored without its audit row")
	}
}

func TestTheReviewQueueListsTheOrganisationsDocumentsOldestFirst(t *testing.T) {
	re := newReviewEnv(t)
	older := re.store.addDocument(re.patient, re.userID, "older.pdf")
	at := recordTime.Add(-time.Hour)
	re.store.insights[older] = &db.Insight{DocumentID: older, Status: "needs_review", UpdatedAt: &at}
	re.store.flagged[older] = 3
	done := re.store.addDocument(re.patient, re.userID, "done.pdf")
	re.store.insights[done] = &db.Insight{DocumentID: done, Status: "verified"}
	theirs := re.store.addDocument(re.store.addPatient("org-2", "OT-1"), re.userID, "theirs.pdf")
	re.store.insights[theirs] = &db.Insight{DocumentID: theirs, Status: "needs_review"}

	rec := re.do(http.MethodGet, "/review/queue", re.auth, nil, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
	resp := decodeJSON[struct {
		Documents []db.ReviewQueueEntry `json:"documents"`
	}](t, rec)
	if len(resp.Documents) != 2 || resp.Documents[0].DocumentID != older || resp.Documents[0].Flagged != 3 ||
		resp.Documents[1].DocumentID != re.doc {
		t.Fatalf("queue = %+v", resp.Documents)
	}
	ev := re.store.audit[len(re.store.audit)-1]
	if ev.Action != actReviewQueueViewed || ev.Details["documents"] != 2 {
		t.Errorf("audit = %+v", ev)
	}
	// Both documents are one patient's: listed once, by id, never by name.
	if ids, _ := ev.Details["patient_ids"].([]string); len(ids) != 1 || ids[0] != re.patient.ID {
		t.Errorf("patient_ids = %v", ev.Details["patient_ids"])
	}
	raw, _ := json.Marshal(ev.Details)
	if strings.Contains(string(raw), "SH-1") {
		t.Errorf("audit details hold the MRN: %s", raw)
	}
}

func TestTheReviewQueueFailsClosedWhenItCantBeAudited(t *testing.T) {
	re := newReviewEnv(t)
	re.store.failAudit = errTest
	if rec := re.do(http.MethodGet, "/review/queue", re.auth, nil, ""); rec.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d", rec.Code)
	}
}
