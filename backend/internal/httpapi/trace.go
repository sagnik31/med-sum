package httpapi

import (
	"log"
	"net/http"
	"strings"
	"time"

	"backend/internal/authz"
	"backend/internal/db"
)

// The trace screen: how the pipeline produced a document's record.
// Clinicians of the organisation see a document's runs in full (inputs,
// outputs, prompts, findings), each view audited as a read of patient data.
// Admins see their organisation's runs with no content at all, on routes of
// their own. Neither can run anything: a step that can be replayed comes
// with the command an operator runs (python -m pipeline.replay, from
// scripts/src), never a button. See docs/adr/0006-durable-traceable-runs.md.

const (
	actTraceViewed = "trace_viewed"
	actRunsListed  = "pipeline_runs_listed"
	actRunViewed   = "pipeline_run_viewed"
	targetRun      = "pipeline_run"
)

// replayCommand is the command that replays a step, for steps that can be.
func replayCommand(stepID string, replayable bool) *string {
	if !replayable {
		return nil
	}
	c := "python -m pipeline.replay " + stepID
	return &c
}

type traceStepOut struct {
	db.TraceStep
	ReplayCommand *string `json:"replay_command"`
}

type orgStepOut struct {
	db.OrgStep
	ReplayCommand *string `json:"replay_command"`
}

// ----- GET /documents/{id}/trace[?run=<run id>] -----

// handleGetTrace returns the document's runs, newest first, and one of
// them in full: the latest, or the one ?run= names.
func (s *Server) handleGetTrace(w http.ResponseWriter, r *http.Request, docID string) {
	runID := r.URL.Query().Get("run")
	if runID != "" && !validUUID(runID) {
		http.Error(w, "run must be a run id", http.StatusBadRequest)
		return
	}
	doc := s.loadDocument(w, r, docID)
	if doc == nil {
		return
	}
	t, ok, err := s.db.GetDocumentTrace(r.Context(), docID, runID)
	if err != nil {
		log.Printf("GetDocumentTrace error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if !ok {
		http.Error(w, "this document has no such run", http.StatusNotFound)
		return
	}
	ev := s.documentEvent(r, actTraceViewed, doc)
	if t.Run != nil {
		ev = with(ev, map[string]any{"run_id": t.Run.RunID})
	}
	if !s.recordAudit(w, r, ev) {
		return
	}
	steps := make([]traceStepOut, len(t.Steps))
	for i, st := range t.Steps {
		steps[i] = traceStepOut{st, replayCommand(st.StepID, st.Replayable)}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"document_id": docID,
		"runs":        t.Runs,
		"run":         t.Run,
		"steps":       steps,
		"prompts":     t.Prompts,
	})
}

// ----- GET /admin/runs[?before=<RFC 3339 time>] -----

type runsPage struct {
	Runs       []db.OrgRun `json:"runs"`
	NextBefore *time.Time  `json:"next_before"` // pass as ?before= for older runs
}

// handleListRuns lists the organisation's runs, newest first, without
// any document, patient or content.
func (s *Server) handleListRuns(w http.ResponseWriter, r *http.Request) {
	var before *time.Time
	if v := r.URL.Query().Get("before"); v != "" {
		t, err := time.Parse(time.RFC3339Nano, v)
		if err != nil {
			http.Error(w, "before must be a time (RFC 3339)", http.StatusBadRequest)
			return
		}
		before = &t
	}
	p, _ := principalFromContext(r.Context())
	runs, err := s.db.ListOrgRuns(r.Context(), p.OrgID, before)
	if err != nil {
		log.Printf("ListOrgRuns error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if !s.recordAudit(w, r, with(on(s.auditEvent(r, actRunsListed), targetRun, ""),
		map[string]any{"entries": len(runs)})) {
		return
	}
	page := runsPage{Runs: runs}
	if page.Runs == nil {
		page.Runs = []db.OrgRun{}
	}
	if len(runs) == db.MaxRunsPage {
		last := runs[len(runs)-1].StartedAt
		page.NextBefore = &last
	}
	writeJSON(w, http.StatusOK, page)
}

// ----- GET /admin/runs/{id} -----

func (s *Server) handleRunByID(w http.ResponseWriter, r *http.Request) {
	// The permission comes first, so a clinician learns nothing about
	// which ids exist.
	if !s.permit(w, r, authz.RunsRead) {
		return
	}
	id := strings.TrimPrefix(r.URL.Path, "/admin/runs/")
	if !validUUID(id) {
		http.NotFound(w, r)
		return
	}
	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	p, _ := principalFromContext(r.Context())
	run, steps, err := s.db.GetOrgRun(r.Context(), p.OrgID, id)
	if err != nil {
		log.Printf("GetOrgRun error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if run == nil {
		http.NotFound(w, r)
		return
	}
	if !s.recordAudit(w, r, on(s.auditEvent(r, actRunViewed), targetRun, id)) {
		return
	}
	out := make([]orgStepOut, len(steps))
	for i, st := range steps {
		out[i] = orgStepOut{st, replayCommand(st.StepID, st.Replayable)}
	}
	writeJSON(w, http.StatusOK, map[string]any{"run": run, "steps": out})
}
