package db

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// A run of document d with five steps: a transcribe whose prompt text is
// stored, a parse whose prompt is not, a fixed repair that stored its
// problems, a repair with tools, and a validate with an error and an info
// finding. Returns the run id and the step ids by seq.
func mustTraceRun(t *testing.T, s *Store, docID string, at time.Time) (string, map[int]string) {
	t.Helper()
	ctx := context.Background()
	hash := "sha256:" + strings.Repeat("b", 64)
	if _, err := s.pool.Exec(ctx, `INSERT INTO prompts (hash, name, text) VALUES ($1, 'extract_page', 'Read the page.')
ON CONFLICT DO NOTHING`, hash); err != nil {
		t.Fatal(err)
	}
	var run string
	if err := s.pool.QueryRow(ctx, `INSERT INTO pipeline_runs (document_id, graph_version, started_at, ended_at, end_state, code_version)
VALUES ($1, 'document-graph-4', $2, $3, 'needs_review', 'abc123') RETURNING id`, docID, at, at.Add(time.Minute)).Scan(&run); err != nil {
		t.Fatal(err)
	}
	findings := `[{"validator": "source_text", "severity": "error", "message": "value 10.4 not on line 8",
	              "item_id": "obs-1", "field": "value_text"},
	             {"validator": "coverage", "severity": "info", "message": "line 9 unused"}]`
	steps := map[int]string{}
	for _, st := range []struct {
		seq                      int
		node, prompt, promptHash string
		input, findings          string
	}{
		{1, "transcribe", "extract_page", hash, `{"page": 1}`, `[]`},
		{2, "parse", "parse_lab_report", "sha256:" + strings.Repeat("c", 64), `{"doc_type": "lab_report"}`, `[]`},
		{3, "repair", "extract_page+repair_parse", hash, `{"repairs": [], "system_prompt_hash": "x"}`, `[]`},
		{4, "repair", "extract_page+repair_parse+tools", hash, `{"repairs": [], "system_prompt_hash": "x"}`, `[]`},
		{5, "validate", "", "", `{"pages": [1]}`, findings},
	} {
		var id string
		if err := s.pool.QueryRow(ctx, `
INSERT INTO pipeline_steps (run_id, seq, node, attempt, started_at, ended_at, status, model, model_role,
                            prompt_name, prompt_hash, input, output, findings, error)
VALUES ($1, $2, $3, 1, $4, $5, 'ok', 'qwen3.5:4b', 'primary', NULLIF($6, ''), NULLIF($7, ''), $8,
        '{"text": "Haemoglobin 10.4"}', $9, NULL)
RETURNING id`, run, st.seq, st.node, at, at.Add(time.Second), st.prompt, st.promptHash, st.input, st.findings).Scan(&id); err != nil {
			t.Fatal(err)
		}
		steps[st.seq] = id
	}
	return run, steps
}

func TestTheTraceIsADocumentsRunsInFull(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	d := mustDocument(t, s, mustPatient(t, s, me.OrgID, me.ID, "SH-1"), me, "a.pdf")

	tr, ok, err := s.GetDocumentTrace(ctx, d.ID, "")
	if err != nil || !ok || tr.Run != nil || len(tr.Runs) != 0 {
		t.Fatalf("before a run: %+v %v %v", tr, ok, err)
	}
	t0 := time.Date(2026, 11, 16, 9, 0, 0, 0, time.UTC)
	old, _ := mustTraceRun(t, s, d.ID, t0)
	latest, steps := mustTraceRun(t, s, d.ID, t0.Add(time.Hour))

	tr, ok, err = s.GetDocumentTrace(ctx, d.ID, "")
	if err != nil || !ok {
		t.Fatal(ok, err)
	}
	if len(tr.Runs) != 2 || tr.Runs[0].RunID != latest || tr.Run.RunID != latest || tr.Run.Steps != 5 ||
		*tr.Run.CodeVersion != "abc123" || len(tr.Steps) != 5 {
		t.Fatalf("trace = %+v", tr)
	}
	// Content: the input, the output and the findings with their messages.
	v := tr.Steps[4]
	if !strings.Contains(string(v.Output), "Haemoglobin 10.4") || !strings.Contains(string(v.Findings), "value 10.4 not on line 8") ||
		*v.DurationMS != 1000 {
		t.Errorf("validate step = %+v", v)
	}
	// Replayable: the model steps whose prompt text is stored; a repair
	// only without tools.
	got := map[string]bool{}
	for _, st := range tr.Steps {
		got[st.StepID] = st.Replayable
	}
	want := map[int]bool{1: true, 2: false, 3: true, 4: false, 5: false}
	for seq, id := range steps {
		if got[id] != want[seq] {
			t.Errorf("step %d replayable = %v, want %v", seq, got[id], want[seq])
		}
	}
	if len(tr.Prompts) != 1 || tr.Prompts["sha256:"+strings.Repeat("b", 64)].Text != "Read the page." {
		t.Errorf("prompts = %+v", tr.Prompts)
	}

	tr, ok, err = s.GetDocumentTrace(ctx, d.ID, old)
	if err != nil || !ok || tr.Run.RunID != old {
		t.Errorf("the older run: %+v %v %v", tr.Run, ok, err)
	}
	other := mustDocument(t, s, mustPatient(t, s, me.OrgID, me.ID, "SH-2"), me, "b.pdf")
	if _, ok, err := s.GetDocumentTrace(ctx, other.ID, old); err != nil || ok {
		t.Errorf("another document's run: %v %v", ok, err)
	}
}

func TestAdminsSeeTheOrganisationsRunsWithoutContent(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	d := mustDocument(t, s, mustPatient(t, s, me.OrgID, me.ID, "SH-1"), me, "a.pdf")
	t0 := time.Date(2026, 11, 16, 9, 0, 0, 0, time.UTC)
	run, _ := mustTraceRun(t, s, d.ID, t0)
	orgB, them := mustOtherOrg(t, s)
	theirDoc := mustDocument(t, s, mustPatient(t, s, orgB, them.ID, "OT-1"), them, "c.pdf")
	theirRun, _ := mustTraceRun(t, s, theirDoc.ID, t0)

	runs, err := s.ListOrgRuns(ctx, me.OrgID, nil)
	if err != nil || len(runs) != 1 || runs[0].RunID != run || runs[0].Steps != 5 {
		t.Fatalf("runs = %+v %v", runs, err)
	}
	if later, err := s.ListOrgRuns(ctx, me.OrgID, &t0); err != nil || len(later) != 0 {
		t.Errorf("before the first run: %+v %v", later, err)
	}
	r, steps, err := s.GetOrgRun(ctx, me.OrgID, run)
	if err != nil || r == nil || len(steps) != 5 {
		t.Fatalf("run = %+v %d %v", r, len(steps), err)
	}
	// No content anywhere in what an admin is sent.
	body, _ := json.Marshal(map[string]any{"runs": runs, "run": r, "steps": steps})
	for _, content := range []string{"Haemoglobin", "10.4 not on line", "line 9 unused", "SH-1", "Patient", d.ID, "a.pdf"} {
		if strings.Contains(string(body), content) {
			t.Errorf("admin view holds %q: %s", content, body)
		}
	}
	var failures []map[string]any
	if err := json.Unmarshal(steps[4].Failures, &failures); err != nil {
		t.Fatal(err)
	}
	if len(failures) != 1 || failures[0]["validator"] != "source_text" || failures[0]["field"] != "value_text" {
		t.Errorf("failures = %s", steps[4].Failures)
	}
	if !steps[0].Replayable || steps[3].Replayable {
		t.Errorf("replayable = %v %v", steps[0].Replayable, steps[3].Replayable)
	}

	if r, _, err := s.GetOrgRun(ctx, me.OrgID, theirRun); err != nil || r != nil {
		t.Errorf("another organisation's run: %+v %v", r, err)
	}
}

func TestADeletedDocumentsRunsLeaveBothViews(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	d := mustDocument(t, s, mustPatient(t, s, me.OrgID, me.ID, "SH-1"), me, "a.pdf")
	run, _ := mustTraceRun(t, s, d.ID, time.Now())
	if _, err := s.SoftDeleteDocument(ctx, me.OrgID, d.ID, me.ID, time.Now(), event(me.OrgID, me.ID, "document_deleted")); err != nil {
		t.Fatal(err)
	}
	if runs, err := s.ListOrgRuns(ctx, me.OrgID, nil); err != nil || len(runs) != 0 {
		t.Errorf("runs = %+v %v", runs, err)
	}
	if r, _, err := s.GetOrgRun(ctx, me.OrgID, run); err != nil || r != nil {
		t.Errorf("run = %+v %v", r, err)
	}
	if tr, _, err := s.GetDocumentTrace(ctx, d.ID, ""); err != nil || len(tr.Runs) != 0 {
		t.Errorf("trace = %+v %v", tr, err)
	}
}
