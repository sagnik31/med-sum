package db

import (
	"context"
	"encoding/json"
	"testing"
	"time"
)

func TestProcessingIsTheLatestRunsStepsWithoutContent(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "me@hospital.test")
	d := mustDocument(t, s, mustPatient(t, s, me.OrgID, me.ID, "SH-1"), me, "a.pdf")

	if run, err := s.GetProcessing(ctx, d.ID); err != nil || run != nil {
		t.Fatalf("before a run: %+v %v", run, err)
	}
	t0 := time.Date(2026, 11, 10, 9, 0, 0, 0, time.UTC)
	var old, latest string
	for i, id := range []*string{&old, &latest} {
		if err := s.pool.QueryRow(ctx, `INSERT INTO pipeline_runs (document_id, graph_version, started_at)
VALUES ($1, 'document-graph-4', $2) RETURNING id`, d.ID, t0.Add(time.Duration(i)*time.Hour)).Scan(id); err != nil {
			t.Fatal(err)
		}
	}
	findings := `[{"validator": "source_text", "severity": "error", "message": "value 10.4 not on line 8",
	              "item_id": "obs-1", "field": "value_text"},
	             {"validator": "coverage", "severity": "info", "message": "line 9 unused"}]`
	var step string
	if err := s.pool.QueryRow(ctx, `
INSERT INTO pipeline_steps (run_id, seq, node, attempt, started_at, ended_at, status, model, model_role, findings, input, output)
VALUES ($1, 2, 'validate', 1, $2, $3, 'failed', 'qwen3.5:4b', 'primary', $4, '{"text": "Asha Rao"}', '{"x": 1}')
RETURNING id`, latest, t0, t0.Add(1500*time.Millisecond), findings).Scan(&step); err != nil {
		t.Fatal(err)
	}
	if _, err := s.pool.Exec(ctx, `
INSERT INTO pipeline_steps (run_id, seq, node, attempt, started_at, ended_at, status) VALUES
($1, 1, 'transcribe', 1, $2, $2, 'ok'), ($3, 1, 'transcribe', 1, $2, $2, 'ok')`, latest, t0, old); err != nil {
		t.Fatal(err)
	}
	if _, err := s.pool.Exec(ctx, `
INSERT INTO pipeline_model_calls (id, step_id, run_id, seq, started_at, ended_at, model, role, status, wall_ms, input_chars, output_chars)
VALUES (gen_random_uuid(), $1, $2, 1, $3, $3, 'qwen3.5:4b', 'primary', 'ok', 10, 1, 1)`, step, latest, t0); err != nil {
		t.Fatal(err)
	}

	run, err := s.GetProcessing(ctx, d.ID)
	if err != nil {
		t.Fatal(err)
	}
	if run.RunID != latest || run.Runs != 2 || len(run.Steps) != 2 || run.Steps[0].Node != "transcribe" {
		t.Fatalf("run = %+v", run)
	}
	v := run.Steps[1]
	if v.Status != "failed" || *v.DurationMS != 1500 || v.ModelCalls != 1 || *v.ModelRole != "primary" {
		t.Errorf("step = %+v", v)
	}
	var failures []map[string]any
	if err := json.Unmarshal(v.Failures, &failures); err != nil {
		t.Fatal(err)
	}
	// The error, not the info finding; and never the message.
	if len(failures) != 1 || failures[0]["validator"] != "source_text" || failures[0]["field"] != "value_text" ||
		failures[0]["message"] != nil {
		t.Errorf("failures = %s", v.Failures)
	}
}
