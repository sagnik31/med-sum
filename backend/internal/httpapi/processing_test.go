package httpapi

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"backend/internal/db"
)

func TestProcessingDetailsAreTheLatestRunsStepsAndAudited(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, "a.pdf")

	rec := e.do(http.MethodGet, "/documents/"+doc+"/processing", auth, nil, "")
	if rec.Code != http.StatusOK || rec.Body.String() != `{"document_id":"`+doc+`","run":null}`+"\n" {
		t.Fatalf("before a run: %d %s", rec.Code, rec.Body)
	}

	model, role := "qwen3.5:4b", "primary"
	ms := int64(1200)
	e.store.processing[doc] = &db.ProcessingRun{RunID: "r1", GraphVersion: "g", StartedAt: time.Now(), Runs: 2,
		Steps: []db.ProcessingStep{{Seq: 1, Node: "parse", Attempt: 2, Status: "ok", Model: &model,
			ModelRole: &role, DurationMS: &ms, ModelCalls: 1,
			Failures: json.RawMessage(`[{"validator":"source_text","severity":"error","item_id":"obs-1","field":"value_text"}]`)}}}
	rec = e.do(http.MethodGet, "/documents/"+doc+"/processing", auth, nil, "")
	resp := decodeJSON[struct {
		Run db.ProcessingRun `json:"run"`
	}](t, rec)
	if resp.Run.Runs != 2 || len(resp.Run.Steps) != 1 || resp.Run.Steps[0].Attempt != 2 ||
		*resp.Run.Steps[0].Model != model {
		t.Errorf("run = %+v", resp.Run)
	}
	if ev := e.store.audit[len(e.store.audit)-1]; ev.Action != actProcessingViewed || *ev.TargetID != doc {
		t.Errorf("audit = %+v", ev)
	}
}

func TestProcessingDetailsStayInTheOrganisationAndFailClosed(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	theirs := e.store.addDocument(e.store.addPatient("org-2", "OT-1"), me, "a.pdf")
	if rec := e.do(http.MethodGet, "/documents/"+theirs+"/processing", auth, nil, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("another organisation: %d", rec.Code)
	}
	mine := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, "b.pdf")
	e.store.failAudit = errTest
	if rec := e.do(http.MethodGet, "/documents/"+mine+"/processing", auth, nil, ""); rec.Code != http.StatusInternalServerError {
		t.Fatalf("audit failure: %d", rec.Code)
	}
}
