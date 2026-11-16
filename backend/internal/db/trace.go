package db

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
)

// The trace screen: a document's pipeline runs in full for clinicians, and
// an organisation's runs without content for admins. Both read views
// (document_trace_*, org_pipeline_*), never the pipeline tables.
// See docs/adr/0006-durable-traceable-runs.md.

// TraceRun is one run of a document: what produced it and how it ended.
type TraceRun struct {
	RunID        string     `json:"run_id"`
	GraphVersion string     `json:"graph_version"`
	StartedAt    time.Time  `json:"started_at"`
	EndedAt      *time.Time `json:"ended_at"`
	EndState     *string    `json:"end_state"`
	ModelsUsed   []string   `json:"models_used"`
	Error        *string    `json:"error"` // masked
	CodeVersion  *string    `json:"code_version"`
	CodeDirty    *bool      `json:"code_dirty"`
	SchemaHash   *string    `json:"schema_hash"`
	DBSchemaHash *string    `json:"db_schema_hash"`
	Steps        int        `json:"steps"`
	FailedSteps  int        `json:"failed_steps"`
}

// TraceStep is one step of a run in full: what went in, what came out, the
// validator findings with their messages, and the prompt by its hash.
type TraceStep struct {
	StepID           string          `json:"step_id"`
	ParentStepID     *string         `json:"parent_step_id"`
	Seq              int             `json:"seq"`
	Node             string          `json:"node"`
	Attempt          int             `json:"attempt"`
	Page             *int            `json:"page"`
	Status           string          `json:"status"`
	Model            *string         `json:"model"`
	ModelRole        *string         `json:"model_role"`
	StartedAt        time.Time       `json:"started_at"`
	EndedAt          *time.Time      `json:"ended_at"`
	DurationMS       *int64          `json:"duration_ms"`
	PromptTokens     *int            `json:"prompt_tokens"`
	CompletionTokens *int            `json:"completion_tokens"`
	ModelCalls       int             `json:"model_calls"`
	PromptName       *string         `json:"prompt_name"`
	PromptHash       *string         `json:"prompt_hash"`
	Input            json.RawMessage `json:"input"`
	Output           json.RawMessage `json:"output"`
	Findings         json.RawMessage `json:"findings"`
	Error            *string         `json:"error"` // masked
	Replayable       bool            `json:"replayable"`
}

// TracePrompt is the text of a prompt the run's steps used.
type TracePrompt struct {
	Name string `json:"name"`
	Text string `json:"text"`
}

// DocumentTrace is a document's runs (newest first) and one run in full.
type DocumentTrace struct {
	Runs    []TraceRun             `json:"runs"`
	Run     *TraceRun              `json:"run"` // nil before the document's first run
	Steps   []TraceStep            `json:"steps"`
	Prompts map[string]TracePrompt `json:"prompts"` // by hash
}

const traceRunColumns = `run_id::text, graph_version, started_at, ended_at, end_state, models_used, error,
       code_version, code_dirty, schema_hash, db_schema_hash, steps, failed_steps`

// GetDocumentTrace returns a document's runs and, in full, the run with
// runID (the latest when runID is ""). ok is false when runID names no run
// of this document. Callers load the document first (GetDocument), which is
// where the organisation is checked.
func (s *Store) GetDocumentTrace(ctx context.Context, docID, runID string) (*DocumentTrace, bool, error) {
	rows, err := s.pool.Query(ctx, `SELECT `+traceRunColumns+`
FROM document_trace_runs WHERE document_id = $1
ORDER BY started_at DESC, run_id DESC`, docID)
	if err != nil {
		return nil, false, err
	}
	t := &DocumentTrace{Runs: []TraceRun{}, Steps: []TraceStep{}, Prompts: map[string]TracePrompt{}}
	for rows.Next() {
		var r TraceRun
		if err := rows.Scan(&r.RunID, &r.GraphVersion, &r.StartedAt, &r.EndedAt, &r.EndState, &r.ModelsUsed,
			&r.Error, &r.CodeVersion, &r.CodeDirty, &r.SchemaHash, &r.DBSchemaHash, &r.Steps, &r.FailedSteps); err != nil {
			rows.Close()
			return nil, false, err
		}
		t.Runs = append(t.Runs, r)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return nil, false, err
	}
	for i := range t.Runs {
		if runID == "" || t.Runs[i].RunID == runID {
			t.Run = &t.Runs[i]
			break
		}
	}
	if t.Run == nil {
		return t, runID == "", nil
	}

	rows, err = s.pool.Query(ctx, `
SELECT step_id::text, parent_step_id::text, seq, node, attempt, page, status, model, model_role, started_at,
       ended_at, duration_ms, prompt_tokens, completion_tokens, model_calls, prompt_name, prompt_hash,
       input, output, findings, error, replayable
FROM document_trace_steps WHERE document_id = $1 AND run_id = $2
ORDER BY seq`, docID, t.Run.RunID)
	if err != nil {
		return nil, false, err
	}
	hashes := map[string]bool{}
	for rows.Next() {
		var st TraceStep
		var input, output, findings []byte
		if err := rows.Scan(&st.StepID, &st.ParentStepID, &st.Seq, &st.Node, &st.Attempt, &st.Page, &st.Status,
			&st.Model, &st.ModelRole, &st.StartedAt, &st.EndedAt, &st.DurationMS, &st.PromptTokens,
			&st.CompletionTokens, &st.ModelCalls, &st.PromptName, &st.PromptHash, &input, &output, &findings,
			&st.Error, &st.Replayable); err != nil {
			rows.Close()
			return nil, false, err
		}
		st.Input, st.Output, st.Findings = rawOrNull(input), rawOrNull(output), rawOrNull(findings)
		if st.PromptHash != nil {
			hashes[*st.PromptHash] = true
		}
		t.Steps = append(t.Steps, st)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return nil, false, err
	}

	rows, err = s.pool.Query(ctx, `SELECT hash, name, text FROM document_trace_prompts WHERE document_id = $1`, docID)
	if err != nil {
		return nil, false, err
	}
	defer rows.Close()
	for rows.Next() {
		var hash string
		var p TracePrompt
		if err := rows.Scan(&hash, &p.Name, &p.Text); err != nil {
			return nil, false, err
		}
		if hashes[hash] { // only the prompts of the run shown
			t.Prompts[hash] = p
		}
	}
	return t, true, rows.Err()
}

func rawOrNull(b []byte) json.RawMessage {
	if b == nil {
		return json.RawMessage("null")
	}
	return json.RawMessage(b)
}

// OrgRun is a run as an admin sees it: no document, patient or content.
type OrgRun struct {
	RunID        string     `json:"run_id"`
	GraphVersion string     `json:"graph_version"`
	StartedAt    time.Time  `json:"started_at"`
	EndedAt      *time.Time `json:"ended_at"`
	EndState     *string    `json:"end_state"`
	ModelsUsed   []string   `json:"models_used"`
	HasError     bool       `json:"has_error"`
	CodeVersion  *string    `json:"code_version"`
	CodeDirty    *bool      `json:"code_dirty"`
	Steps        int        `json:"steps"`
	FailedSteps  int        `json:"failed_steps"`
}

// OrgStep is a step as an admin sees it: how it went, never what it read
// or wrote. Failures name each failing finding's validator, severity,
// item and field, never its message.
type OrgStep struct {
	StepID           string          `json:"step_id"`
	ParentStepID     *string         `json:"parent_step_id"`
	Seq              int             `json:"seq"`
	Node             string          `json:"node"`
	Attempt          int             `json:"attempt"`
	Page             *int            `json:"page"`
	Status           string          `json:"status"`
	Model            *string         `json:"model"`
	ModelRole        *string         `json:"model_role"`
	StartedAt        time.Time       `json:"started_at"`
	EndedAt          *time.Time      `json:"ended_at"`
	DurationMS       *int64          `json:"duration_ms"`
	PromptTokens     *int            `json:"prompt_tokens"`
	CompletionTokens *int            `json:"completion_tokens"`
	ModelCalls       int             `json:"model_calls"`
	PromptName       *string         `json:"prompt_name"`
	Failures         json.RawMessage `json:"failures"`
	HasError         bool            `json:"has_error"`
	Replayable       bool            `json:"replayable"`
}

// MaxRunsPage is how many runs one page of the admin list holds.
const MaxRunsPage = 200

const orgRunColumns = `run_id::text, graph_version, started_at, ended_at, end_state, models_used, has_error,
       code_version, code_dirty, steps, failed_steps`

func scanOrgRun(row interface{ Scan(...any) error }) (OrgRun, error) {
	var r OrgRun
	err := row.Scan(&r.RunID, &r.GraphVersion, &r.StartedAt, &r.EndedAt, &r.EndState, &r.ModelsUsed,
		&r.HasError, &r.CodeVersion, &r.CodeDirty, &r.Steps, &r.FailedSteps)
	return r, err
}

// ListOrgRuns returns an organisation's runs, newest first, started before
// `before` when it is set, at most MaxRunsPage.
func (s *Store) ListOrgRuns(ctx context.Context, orgID string, before *time.Time) ([]OrgRun, error) {
	rows, err := s.pool.Query(ctx, `SELECT `+orgRunColumns+`
FROM org_pipeline_runs
WHERE org_id = $1 AND ($2::timestamptz IS NULL OR started_at < $2)
ORDER BY started_at DESC, run_id DESC
LIMIT $3`, orgID, before, MaxRunsPage)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []OrgRun{}
	for rows.Next() {
		r, err := scanOrgRun(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// GetOrgRun returns one of the organisation's runs and its steps, or nil
// when the organisation has no such run.
func (s *Store) GetOrgRun(ctx context.Context, orgID, runID string) (*OrgRun, []OrgStep, error) {
	r, err := scanOrgRun(s.pool.QueryRow(ctx, `SELECT `+orgRunColumns+`
FROM org_pipeline_runs WHERE org_id = $1 AND run_id = $2`, orgID, runID))
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil, nil
	}
	if err != nil {
		return nil, nil, err
	}
	rows, err := s.pool.Query(ctx, `
SELECT step_id::text, parent_step_id::text, seq, node, attempt, page, status, model, model_role, started_at,
       ended_at, duration_ms, prompt_tokens, completion_tokens, model_calls, prompt_name, failures, has_error,
       replayable
FROM org_pipeline_steps WHERE org_id = $1 AND run_id = $2
ORDER BY seq`, orgID, runID)
	if err != nil {
		return nil, nil, err
	}
	defer rows.Close()
	steps := []OrgStep{}
	for rows.Next() {
		var st OrgStep
		var failures []byte
		if err := rows.Scan(&st.StepID, &st.ParentStepID, &st.Seq, &st.Node, &st.Attempt, &st.Page, &st.Status,
			&st.Model, &st.ModelRole, &st.StartedAt, &st.EndedAt, &st.DurationMS, &st.PromptTokens,
			&st.CompletionTokens, &st.ModelCalls, &st.PromptName, &failures, &st.HasError,
			&st.Replayable); err != nil {
			return nil, nil, err
		}
		st.Failures = rawOrNull(failures)
		steps = append(steps, st)
	}
	return &r, steps, rows.Err()
}
