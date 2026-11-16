package httpapi

import (
	"encoding/json"
	"net/http"
	"sort"
	"strings"
	"testing"
	"time"

	"backend/internal/db"
)

const (
	run1  = "11111111-1111-1111-1111-111111111111"
	run2  = "22222222-2222-2222-2222-222222222222"
	step1 = "33333333-3333-3333-3333-333333333333"
	step2 = "44444444-4444-4444-4444-444444444444"
)

func strp(s string) *string { return &s }

// A document with two runs; the latest has a replayable parse and a validate step.
func addTrace(e *testEnv, doc string) {
	t0 := time.Date(2026, 11, 16, 9, 0, 0, 0, time.UTC)
	hash := "sha256:" + strings.Repeat("a", 64)
	e.store.traces[doc] = &db.DocumentTrace{
		Runs: []db.TraceRun{
			{RunID: run2, GraphVersion: "g", StartedAt: t0.Add(time.Hour), EndState: strp("verified"), Steps: 2},
			{RunID: run1, GraphVersion: "g", StartedAt: t0, EndState: strp("failed"), Steps: 1, FailedSteps: 1},
		},
		Steps: []db.TraceStep{
			{StepID: step1, Seq: 1, Node: "parse", Attempt: 1, Status: "ok", PromptHash: &hash,
				Input: json.RawMessage(`{"doc_type":"lab_report"}`), Output: json.RawMessage(`{"part_results":[]}`),
				Findings: json.RawMessage(`[]`), Replayable: true},
			{StepID: step2, Seq: 2, Node: "validate", Attempt: 1, Status: "ok",
				Input: json.RawMessage(`{}`), Output: json.RawMessage(`{}`),
				Findings: json.RawMessage(`[{"validator":"source_text","severity":"error","message":"value 10.4 not on line 8"}]`)},
		},
		Prompts: map[string]db.TracePrompt{hash: {Name: "parse_lab_report", Text: "Read the page."}},
	}
}

type traceResp struct {
	Runs  []db.TraceRun `json:"runs"`
	Run   *db.TraceRun  `json:"run"`
	Steps []struct {
		db.TraceStep
		ReplayCommand *string `json:"replay_command"`
	} `json:"steps"`
	Prompts map[string]db.TracePrompt `json:"prompts"`
}

func TestAClinicianSeesTheFullTraceAndTheViewIsAudited(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, "a.pdf")

	rec := e.do(http.MethodGet, "/documents/"+doc+"/trace", auth, nil, "")
	before := decodeJSON[traceResp](t, rec)
	if rec.Code != http.StatusOK || before.Run != nil || len(before.Runs) != 0 || len(before.Steps) != 0 {
		t.Fatalf("before a run: %d %s", rec.Code, rec.Body)
	}

	addTrace(e, doc)
	rec = e.do(http.MethodGet, "/documents/"+doc+"/trace", auth, nil, "")
	resp := decodeJSON[traceResp](t, rec)
	if resp.Run == nil || resp.Run.RunID != run2 || len(resp.Runs) != 2 || len(resp.Steps) != 2 {
		t.Fatalf("trace = %s", rec.Body)
	}
	// The full step: its input, output and the finding's message.
	if !strings.Contains(string(resp.Steps[1].Findings), "value 10.4 not on line 8") ||
		string(resp.Steps[0].Input) != `{"doc_type":"lab_report"}` {
		t.Errorf("steps = %s", rec.Body)
	}
	// Replay is a command to copy, only for steps that can be replayed.
	if c := resp.Steps[0].ReplayCommand; c == nil || *c != "python -m pipeline.replay "+step1 {
		t.Errorf("replay command = %v", c)
	}
	if resp.Steps[1].ReplayCommand != nil {
		t.Errorf("validate has a replay command: %v", *resp.Steps[1].ReplayCommand)
	}
	if len(resp.Prompts) != 1 {
		t.Errorf("prompts = %v", resp.Prompts)
	}
	ev := e.store.audit[len(e.store.audit)-1]
	if ev.Action != actTraceViewed || deref(ev.TargetID) != doc || ev.PatientID == nil || ev.Details["run_id"] != run2 {
		t.Errorf("audit = %+v", ev)
	}

	rec = e.do(http.MethodGet, "/documents/"+doc+"/trace?run="+run1, auth, nil, "")
	if r := decodeJSON[traceResp](t, rec); r.Run == nil || r.Run.RunID != run1 {
		t.Errorf("?run=: %s", rec.Body)
	}
}

func TestATraceRunMustBeTheDocumentsAndWellFormed(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, "a.pdf")
	addTrace(e, doc)
	if rec := e.do(http.MethodGet, "/documents/"+doc+"/trace?run=nope", auth, nil, ""); rec.Code != http.StatusBadRequest {
		t.Errorf("malformed run: %d", rec.Code)
	}
	other := "55555555-5555-5555-5555-555555555555"
	if rec := e.do(http.MethodGet, "/documents/"+doc+"/trace?run="+other, auth, nil, ""); rec.Code != http.StatusNotFound {
		t.Errorf("another document's run: %d", rec.Code)
	}
}

func TestTheTraceStaysInTheOrganisationFailsClosedAndIsNotForAdmins(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	theirs := e.store.addDocument(e.store.addPatient("org-2", "OT-1"), me, "a.pdf")
	if rec := e.do(http.MethodGet, "/documents/"+theirs+"/trace", auth, nil, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("another organisation: %d", rec.Code)
	}
	mine := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, "b.pdf")
	_, admin := e.loginWith("admin@hospital.test", db.RoleAdmin)
	if rec := e.do(http.MethodGet, "/documents/"+mine+"/trace", admin, nil, ""); rec.Code != http.StatusForbidden {
		t.Errorf("admin: %d", rec.Code)
	}
	if rec := e.do(http.MethodPost, "/documents/"+mine+"/trace", auth, nil, ""); rec.Code != http.StatusMethodNotAllowed {
		t.Errorf("POST: %d", rec.Code)
	}
	e.store.failAudit = errTest
	if rec := e.do(http.MethodGet, "/documents/"+mine+"/trace", auth, nil, ""); rec.Code != http.StatusInternalServerError {
		t.Fatalf("audit failure: %d", rec.Code)
	}
}

// ----- Admins: the organisation's runs without content -----

func addOrgRuns(e *testEnv) {
	t0 := time.Date(2026, 11, 16, 9, 0, 0, 0, time.UTC)
	e.store.orgRuns["org-1"] = []db.OrgRun{
		{RunID: run2, GraphVersion: "g", StartedAt: t0.Add(time.Hour), EndState: strp("needs_review"), Steps: 2},
		{RunID: run1, GraphVersion: "g", StartedAt: t0, EndState: strp("failed"), HasError: true, Steps: 1},
	}
	e.store.orgSteps[run2] = []db.OrgStep{
		{StepID: step1, Seq: 1, Node: "parse", Status: "ok", Failures: json.RawMessage(`[]`), Replayable: true},
		{StepID: step2, Seq: 2, Node: "validate", Status: "ok",
			Failures: json.RawMessage(`[{"validator":"source_text","severity":"error","item_id":"obs-1","field":"value_text"}]`)},
	}
	e.store.orgRuns["org-2"] = []db.OrgRun{{RunID: "66666666-6666-6666-6666-666666666666", StartedAt: t0}}
}

func keys(m map[string]any) []string {
	out := []string{}
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func TestAnAdminListsTheOrganisationsRunsAndTheListIsAudited(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith("admin@hospital.test", db.RoleAdmin)
	addOrgRuns(e)
	rec := e.do(http.MethodGet, "/admin/runs", admin, nil, "")
	page := decodeJSON[struct {
		Runs       []map[string]any `json:"runs"`
		NextBefore *time.Time       `json:"next_before"`
	}](t, rec)
	if rec.Code != http.StatusOK || len(page.Runs) != 2 || page.NextBefore != nil {
		t.Fatalf("runs: %d %s", rec.Code, rec.Body)
	}
	want := "code_dirty code_version end_state ended_at failed_steps graph_version has_error models_used run_id started_at steps"
	if got := strings.Join(keys(page.Runs[0]), " "); got != want {
		t.Errorf("run fields = %s", got)
	}
	ev := e.store.audit[len(e.store.audit)-1]
	if ev.Action != actRunsListed || ev.PatientID != nil || ev.Details["entries"] != 2 {
		t.Errorf("audit = %+v", ev)
	}

	before := "2026-11-16T09:30:00Z"
	rec = e.do(http.MethodGet, "/admin/runs?before="+before, admin, nil, "")
	if p := decodeJSON[struct {
		Runs []db.OrgRun `json:"runs"`
	}](t, rec); len(p.Runs) != 1 || p.Runs[0].RunID != run1 {
		t.Errorf("before: %s", rec.Body)
	}
	if rec := e.do(http.MethodGet, "/admin/runs?before=yesterday", admin, nil, ""); rec.Code != http.StatusBadRequest {
		t.Errorf("bad before: %d", rec.Code)
	}
}

func TestAnAdminSeesARunsStepsWithoutContent(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith("admin@hospital.test", db.RoleAdmin)
	addOrgRuns(e)
	rec := e.do(http.MethodGet, "/admin/runs/"+run2, admin, nil, "")
	resp := decodeJSON[struct {
		Run   map[string]any   `json:"run"`
		Steps []map[string]any `json:"steps"`
	}](t, rec)
	if rec.Code != http.StatusOK || len(resp.Steps) != 2 {
		t.Fatalf("run: %d %s", rec.Code, rec.Body)
	}
	want := "attempt completion_tokens duration_ms ended_at failures has_error model model_calls model_role node page " +
		"parent_step_id prompt_name prompt_tokens replay_command replayable seq started_at status step_id"
	if got := strings.Join(keys(resp.Steps[0]), " "); got != want {
		t.Errorf("step fields = %s", got)
	}
	if resp.Steps[0]["replay_command"] != "python -m pipeline.replay "+step1 || resp.Steps[1]["replay_command"] != nil {
		t.Errorf("replay commands = %v / %v", resp.Steps[0]["replay_command"], resp.Steps[1]["replay_command"])
	}
	ev := e.store.audit[len(e.store.audit)-1]
	if ev.Action != actRunViewed || deref(ev.TargetType) != targetRun || deref(ev.TargetID) != run2 {
		t.Errorf("audit = %+v", ev)
	}
}

func TestRunsAreTheOrganisationsAndOnlyForAdmins(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith("admin@hospital.test", db.RoleAdmin)
	_, clinician := e.login("me@hospital.test")
	addOrgRuns(e)
	if rec := e.do(http.MethodGet, "/admin/runs/66666666-6666-6666-6666-666666666666", admin, nil, ""); rec.Code != http.StatusNotFound {
		t.Errorf("another organisation's run: %d", rec.Code)
	}
	if rec := e.do(http.MethodGet, "/admin/runs/nope", admin, nil, ""); rec.Code != http.StatusNotFound {
		t.Errorf("malformed id: %d", rec.Code)
	}
	for _, path := range []string{"/admin/runs", "/admin/runs/" + run2} {
		if rec := e.do(http.MethodGet, path, clinician, nil, ""); rec.Code != http.StatusForbidden {
			t.Errorf("clinician %s: %d", path, rec.Code)
		}
	}
	e.store.failAudit = errTest
	for _, path := range []string{"/admin/runs", "/admin/runs/" + run2} {
		if rec := e.do(http.MethodGet, path, admin, nil, ""); rec.Code != http.StatusInternalServerError {
			t.Errorf("audit failure %s: %d", path, rec.Code)
		}
	}
}
