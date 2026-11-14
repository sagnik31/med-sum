package db

import (
	"context"
	"encoding/json"
	"time"
)

// ProcessingStep is one step of a document's latest pipeline run, as the
// "Processing details" panel shows it: no document content, only how the
// step went. See docs/adr/0006-durable-traceable-runs.md.
type ProcessingStep struct {
	Seq        int             `json:"seq"`
	Node       string          `json:"node"`
	Attempt    int             `json:"attempt"`
	Page       *int            `json:"page"`
	Status     string          `json:"status"`
	Model      *string         `json:"model"`
	ModelRole  *string         `json:"model_role"`
	StartedAt  time.Time       `json:"started_at"`
	EndedAt    *time.Time      `json:"ended_at"`
	DurationMS *int64          `json:"duration_ms"`
	ModelCalls int             `json:"model_calls"`
	Failures   json.RawMessage `json:"failures"` // [{validator, severity, item_id, field}]
	Error      *string         `json:"error"`    // masked
}

// ProcessingRun is a document's latest run and its steps in order.
type ProcessingRun struct {
	RunID        string           `json:"run_id"`
	GraphVersion string           `json:"graph_version"`
	StartedAt    time.Time        `json:"started_at"`
	EndedAt      *time.Time       `json:"ended_at"`
	EndState     *string          `json:"end_state"`
	ModelsUsed   []string         `json:"models_used"`
	Runs         int              `json:"runs"` // every run of the document, this one included
	Steps        []ProcessingStep `json:"steps"`
}

// GetProcessing returns the latest run of a document with its steps, or
// nil when it has never run. Callers load the document first
// (GetDocument), which is where the organisation is checked.
func (s *Store) GetProcessing(ctx context.Context, docID string) (*ProcessingRun, error) {
	rows, err := s.pool.Query(ctx, `
SELECT run_id::text, graph_version, run_started_at, run_ended_at, end_state, models_used, runs,
       seq, node, attempt, page, status, model, model_role, started_at, ended_at, duration_ms,
       model_calls, failures, error
FROM document_processing_steps
WHERE document_id = $1
ORDER BY seq`, docID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var run *ProcessingRun
	for rows.Next() {
		var r ProcessingRun
		var st ProcessingStep
		var failures []byte
		if err := rows.Scan(&r.RunID, &r.GraphVersion, &r.StartedAt, &r.EndedAt, &r.EndState, &r.ModelsUsed,
			&r.Runs, &st.Seq, &st.Node, &st.Attempt, &st.Page, &st.Status, &st.Model, &st.ModelRole,
			&st.StartedAt, &st.EndedAt, &st.DurationMS, &st.ModelCalls, &failures, &st.Error); err != nil {
			return nil, err
		}
		st.Failures = json.RawMessage(failures)
		if run == nil {
			r.Steps = []ProcessingStep{}
			run = &r
		}
		run.Steps = append(run.Steps, st)
	}
	return run, rows.Err()
}
