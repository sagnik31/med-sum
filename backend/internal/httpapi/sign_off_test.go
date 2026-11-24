package httpapi

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"backend/internal/db"
)

// signOff signs the document off as a page that read the record at
// recordUpdatedAt does.
func (re *reviewEnv) signOff(recordUpdatedAt time.Time) *httptest.ResponseRecorder {
	return re.postJSON("/documents/"+re.doc+"/sign-off", re.auth, map[string]any{"record_updated_at": recordUpdatedAt})
}

// No run ends verified: a document that passed every check waits in the
// queue as checks_passed until a clinician signs it off. See
// docs/adr/0013-model-choice-after-the-first-live-evals.md.
func TestADocumentThatPassedItsChecksWaitsForASignOff(t *testing.T) {
	re := newReviewEnv(t)
	re.store.insights[re.doc].Status = "checks_passed"

	resp := decodeJSON[struct {
		Documents []db.ReviewQueueEntry `json:"documents"`
	}](t, re.do(http.MethodGet, "/review/queue", re.auth, nil, ""))
	if len(resp.Documents) != 1 || resp.Documents[0].Reason != "checks_passed" {
		t.Fatalf("queue = %+v", resp.Documents)
	}

	rec := re.signOff(recordTime)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if re.store.insights[re.doc].Status != "verified" {
		t.Error("the document was not verified")
	}
	ev := re.store.audit[len(re.store.audit)-1]
	if ev.Action != actDocumentSignedOff || ev.TargetID == nil || *ev.TargetID != re.doc ||
		ev.PatientID == nil || *ev.PatientID != re.patient.ID {
		t.Errorf("audit = %+v", ev)
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
	resp = decodeJSON[struct {
		Documents []db.ReviewQueueEntry `json:"documents"`
	}](t, re.do(http.MethodGet, "/review/queue", re.auth, nil, ""))
	if len(resp.Documents) != 0 {
		t.Errorf("a signed-off document is still queued: %+v", resp.Documents)
	}

	// Signed off already: nothing left to sign off.
	if rec := re.signOff(recordTime); rec.Code != http.StatusConflict {
		t.Errorf("second sign-off: status = %d", rec.Code)
	}
}

// A review of a document that passed its checks leaves it awaiting the
// sign-off, even with nothing flagged in it.
func TestAReviewDoesNotVerifyADocumentThatPassedItsChecks(t *testing.T) {
	re := newReviewEnv(t)
	re.store.insights[re.doc].Status = "checks_passed"
	re.applied["flagged"] = 0
	re.applied["original"] = nil

	rec := re.review(map[string]any{"action": "confirm", "item_id": "obs-2"})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if resp := decodeJSON[reviewResponse](t, rec); resp.Status != "checks_passed" {
		t.Errorf("status = %s", resp.Status)
	}
	if re.store.insights[re.doc].Status != "checks_passed" {
		t.Errorf("status = %s, want checks_passed", re.store.insights[re.doc].Status)
	}
	if ev := re.store.audit[len(re.store.audit)-1]; ev.Details["verified"] != false {
		t.Errorf("audit = %+v", ev)
	}
}

// The sign-off is of the record the clinician read: after a reprocess, or
// another clinician's correction, the page they have open shows another.
func TestARecordThatChangedSinceItWasReadIsNotSignedOff(t *testing.T) {
	re := newReviewEnv(t)
	re.store.insights[re.doc].Status = "checks_passed"
	re.store.records[re.doc].UpdatedAt = recordTime.Add(time.Minute)
	if rec := re.signOff(recordTime); rec.Code != http.StatusConflict {
		t.Fatalf("stale sign-off: status = %d", rec.Code)
	}
	if re.store.insights[re.doc].Status != "checks_passed" {
		t.Error("a record the clinician never saw was verified")
	}
	// A sign-off that doesn't say what was read is refused.
	if rec := re.postJSON("/documents/"+re.doc+"/sign-off", re.auth, map[string]any{}); rec.Code != http.StatusBadRequest {
		t.Errorf("without record_updated_at: status = %d", rec.Code)
	}
	if rec := re.signOff(recordTime.Add(time.Minute)); rec.Code != http.StatusOK {
		t.Errorf("after reading it again: status = %d: %s", rec.Code, rec.Body)
	}
}

func TestOnlyADocumentThatPassedItsChecksIsSignedOff(t *testing.T) {
	for _, status := range []string{"needs_review", "verified", "processing", "failed"} {
		re := newReviewEnv(t)
		re.store.insights[re.doc].Status = status
		if rec := re.signOff(recordTime); rec.Code != http.StatusConflict {
			t.Errorf("%s: status = %d", status, rec.Code)
		}
		if re.store.insights[re.doc].Status != status {
			t.Errorf("%s became %s", status, re.store.insights[re.doc].Status)
		}
	}
}

// An audit sample is signed off by finishing its audit, which checks
// every value: a plain sign-off would let it skip that.
func TestAnAuditSampleIsNotSignedOffWithoutItsAudit(t *testing.T) {
	re := newReviewEnv(t)
	re.store.insights[re.doc].Status = "checks_passed"
	re.store.auditOpen[re.doc] = true
	rec := re.signOff(recordTime)
	if rec.Code != http.StatusConflict {
		t.Fatalf("status = %d", rec.Code)
	}
	if re.store.insights[re.doc].Status != "checks_passed" {
		t.Error("an audit sample was signed off")
	}
}

func TestAnotherOrganisationsDocumentCantBeSignedOff(t *testing.T) {
	re := newReviewEnv(t)
	theirs := re.store.addDocument(re.store.addPatient("org-2", "OT-1"), re.userID, "b.pdf")
	re.store.insights[theirs] = &db.Insight{DocumentID: theirs, Status: "checks_passed"}
	if rec := re.postJSON("/documents/"+theirs+"/sign-off", re.auth, map[string]any{"record_updated_at": recordTime}); rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d", rec.Code)
	}
	if re.store.insights[theirs].Status != "checks_passed" {
		t.Error("signed off another organisation's document")
	}
}

func TestAnAdminCantSignOffADocument(t *testing.T) {
	re := newReviewEnv(t)
	re.store.insights[re.doc].Status = "checks_passed"
	_, admin := re.loginWith("admin@hospital.test", "admin")
	if rec := re.postJSON("/documents/"+re.doc+"/sign-off", admin, map[string]any{"record_updated_at": recordTime}); rec.Code != http.StatusForbidden {
		t.Fatalf("status = %d", rec.Code)
	}
}

func TestASignOffThatCantBeAuditedIsNotStored(t *testing.T) {
	re := newReviewEnv(t)
	re.store.insights[re.doc].Status = "checks_passed"
	re.store.failAudit = errTest
	if rec := re.signOff(recordTime); rec.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d", rec.Code)
	}
	if re.store.insights[re.doc].Status != "checks_passed" {
		t.Error("the sign-off was stored without its audit row")
	}
}
