package httpapi

import (
	"net/http"
	"strings"
	"testing"
	"time"

	"backend/internal/db"
)

// A verified document sampled for an audit waits in the queue, marked
// audit, until a clinician finishes the audit.
func TestAnAuditSampleWaitsInTheQueueUntilItsAuditIsFinished(t *testing.T) {
	re := newReviewEnv(t)
	sampled := recordTime.Add(time.Hour)
	re.store.insights[re.doc] = &db.Insight{DocumentID: re.doc, Status: "verified", UpdatedAt: &sampled}
	re.store.auditOpen[re.doc] = true

	resp := decodeJSON[struct {
		Documents []db.ReviewQueueEntry `json:"documents"`
	}](t, re.do(http.MethodGet, "/review/queue", re.auth, nil, ""))
	if len(resp.Documents) != 1 || resp.Documents[0].Reason != "audit" {
		t.Fatalf("queue = %+v", resp.Documents)
	}

	rec := re.postJSON("/documents/"+re.doc+"/audit-complete", re.auth, map[string]any{})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	ev := re.store.audit[len(re.store.audit)-1]
	if ev.Action != actAuditCompleted || ev.TargetID == nil || *ev.TargetID != re.doc {
		t.Errorf("audit = %+v", ev)
	}
	resp = decodeJSON[struct {
		Documents []db.ReviewQueueEntry `json:"documents"`
	}](t, re.do(http.MethodGet, "/review/queue", re.auth, nil, ""))
	if len(resp.Documents) != 0 {
		t.Errorf("a finished audit is still queued: %+v", resp.Documents)
	}

	// Finished already: nothing left to finish.
	if rec := re.postJSON("/documents/"+re.doc+"/audit-complete", re.auth, map[string]any{}); rec.Code != http.StatusConflict {
		t.Errorf("second finish: status = %d", rec.Code)
	}
}

func TestAnotherOrganisationsAuditCantBeFinished(t *testing.T) {
	re := newReviewEnv(t)
	theirs := re.store.addDocument(re.store.addPatient("org-2", "OT-1"), re.userID, "b.pdf")
	re.store.insights[theirs] = &db.Insight{DocumentID: theirs, Status: "verified"}
	re.store.auditOpen[theirs] = true
	if rec := re.postJSON("/documents/"+theirs+"/audit-complete", re.auth, map[string]any{}); rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d", rec.Code)
	}
	if !re.store.auditOpen[theirs] {
		t.Error("finished another organisation's audit")
	}
}

func TestAnAdminCantFinishAnAudit(t *testing.T) {
	re := newReviewEnv(t)
	re.store.auditOpen[re.doc] = true
	_, admin := re.loginWith("admin@hospital.test", "admin")
	if rec := re.postJSON("/documents/"+re.doc+"/audit-complete", admin, map[string]any{}); rec.Code != http.StatusForbidden {
		t.Fatalf("status = %d", rec.Code)
	}
}

func TestAnAuditThatCantBeAuditedIsNotFinished(t *testing.T) {
	re := newReviewEnv(t)
	re.store.auditOpen[re.doc] = true
	re.store.failAudit = errTest
	if rec := re.postJSON("/documents/"+re.doc+"/audit-complete", re.auth, map[string]any{}); rec.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d", rec.Code)
	}
	if !re.store.auditOpen[re.doc] {
		t.Error("the audit was finished without its audit row")
	}
}

// An audit is finished only once every value is confirmed or corrected.
func TestAnAuditWithValuesLeftIsNotFinished(t *testing.T) {
	re := newReviewEnv(t)
	re.store.auditOpen[re.doc] = true
	re.store.auditToGo[re.doc] = 3
	rec := re.postJSON("/documents/"+re.doc+"/audit-complete", re.auth, map[string]any{})
	if rec.Code != http.StatusConflict {
		t.Fatalf("status = %d", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "every value") || !strings.Contains(rec.Body.String(), "3 to go") {
		t.Errorf("body = %q", rec.Body)
	}
	if !re.store.auditOpen[re.doc] {
		t.Error("an incomplete audit was finished")
	}
	for _, ev := range re.store.audit {
		if ev.Action == actAuditCompleted {
			t.Error("an incomplete audit was audited as finished")
		}
	}
}
