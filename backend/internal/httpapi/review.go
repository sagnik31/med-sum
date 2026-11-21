package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"time"

	"backend/internal/db"
)

// ----- Clinician review. See docs/adr/0011-clinician-review.md. -----
//
// A clinician confirms an item's reading or corrects one of its fields.
// The AI service applies the review to the record (it owns the record's
// rules and the value checks) and returns the new record; this API checks
// the permission and the organisation before, and stores the result with
// its audit row in one transaction after. The model's reading is never
// overwritten: a correction sits beside it.

const (
	actReviewQueueViewed = "review_queue_viewed"
	actFieldConfirmed    = "field_confirmed"
	actFieldCorrected    = "field_corrected"
	actAuditCompleted    = "audit_completed"
)

// ----- GET /review/queue -----

func (s *Server) handleReviewQueue(w http.ResponseWriter, r *http.Request) {
	p, _ := principalFromContext(r.Context())
	entries, err := s.db.ListReviewQueue(r.Context(), p.OrgID)
	if err != nil {
		log.Printf("ListReviewQueue error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	// The patients the queue showed (ids only), so the audit log's patient
	// filter finds that their names were listed. See
	// docs/adr/0010-staff-access-and-security.md.
	patients := []string{}
	seen := map[string]bool{}
	for _, e := range entries {
		if !seen[e.PatientID] {
			seen[e.PatientID] = true
			patients = append(patients, e.PatientID)
		}
	}
	if !s.recordAudit(w, r, with(s.auditEvent(r, actReviewQueueViewed),
		map[string]any{"documents": len(entries), "patient_ids": patients})) {
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"documents": entries})
}

// ----- POST /documents/{id}/review -----

type reviewRequest struct {
	Action string  `json:"action"` // confirm or correct
	ItemID string  `json:"item_id"`
	Field  *string `json:"field"`
	Value  *string `json:"value"`
	Note   *string `json:"note"`
	// The record's updated_at as the clinician's page read it: a record
	// that changed since (another review, a reprocess) answers 409.
	RecordUpdatedAt time.Time `json:"record_updated_at"`
	// The checks on a corrected value found something, the clinician saw
	// it and saves the value all the same.
	AcceptFindings bool `json:"accept_findings"`
	// Signal ids the clinician acknowledges by name. A review of the
	// document while it carries LC-13 (printed for another patient) or
	// LC-17 (a results row may be missing) needs each acknowledged; a
	// plain confirmation doesn't clear them. The AI service checks which
	// are needed. See docs/adr/0011-clinician-review.md.
	Acknowledge []string `json:"acknowledge"`
}

// reviewFinding is one value check a corrected value failed, as the AI
// service reports it (the message quotes the values, for the clinician).
type reviewFinding struct {
	Validator string  `json:"validator"`
	Severity  string  `json:"severity"`
	Field     *string `json:"field"`
	Message   string  `json:"message"`
}

type reviewApplied struct {
	Record   json.RawMessage `json:"record"`
	HTML     string          `json:"html"`
	Findings []reviewFinding `json:"findings"`
	Signals  []string        `json:"signals"`
	Original *string         `json:"original"`
	Flagged  int             `json:"flagged"`
}

type reviewResponse struct {
	DocumentID      string          `json:"document_id"`
	Status          string          `json:"status"` // needs_review or verified
	RecordUpdatedAt time.Time       `json:"record_updated_at"`
	Flagged         int             `json:"flagged"`
	Findings        []reviewFinding `json:"findings"`
}

// Lengths the AI service also checks; refused here first so an oversized
// body never reaches it.
const (
	maxReviewValue = 500
	maxReviewNote  = 1000
	maxReviewBody  = 16 << 10
	// More than the signals a document can carry at once.
	maxAcknowledged = 8
)

func (s *Server) handleReviewDocument(w http.ResponseWriter, r *http.Request, docID string) {
	ctx := r.Context()
	p, _ := principalFromContext(ctx)
	var req reviewRequest
	if err := json.NewDecoder(io.LimitReader(r.Body, maxReviewBody)).Decode(&req); err != nil {
		http.Error(w, "invalid JSON body", http.StatusBadRequest)
		return
	}
	if msg := req.problem(); msg != "" {
		http.Error(w, msg, http.StatusBadRequest)
		return
	}
	doc := s.loadDocument(w, r, docID)
	if doc == nil {
		return
	}
	rec, err := s.db.GetDocumentRecord(ctx, docID)
	if err != nil {
		log.Printf("GetDocumentRecord error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if rec == nil {
		http.Error(w, "this document has no record yet", http.StatusNotFound)
		return
	}
	ins, err := s.db.GetInsightByDocumentID(ctx, docID)
	if err != nil {
		log.Printf("GetInsightByDocumentID error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if ins == nil || !insightEnded(ins.Status) {
		http.Error(w, "the document is being processed; review it when it is done", http.StatusConflict)
		return
	}
	if !rec.UpdatedAt.Equal(req.RecordUpdatedAt) {
		http.Error(w, "the record changed since you opened it; reload the page", http.StatusConflict)
		return
	}

	now := s.now()
	var applied reviewApplied
	status, detail, err := s.callInsightsServiceJSON(ctx, "/internal/review-apply", map[string]any{
		"record": rec.Record,
		"review": map[string]any{
			"action": req.Action, "item_id": req.ItemID, "field": req.Field,
			"value": req.Value, "note": req.Note, "acknowledge": req.acknowledged(),
			"reviewer": p.FullName, "at": now.UTC().Format(time.RFC3339Nano),
		},
	}, &applied)
	if err != nil {
		log.Printf("review-apply error for document %s: %v", docID, err)
		http.Error(w, "insights service unavailable", http.StatusBadGateway)
		return
	}
	if status == http.StatusUnprocessableEntity {
		// The AI service's reasons name ids and fields, never values.
		http.Error(w, detail, http.StatusUnprocessableEntity)
		return
	}
	if applied.Findings == nil {
		applied.Findings = []reviewFinding{}
	}
	if len(applied.Findings) > 0 && !req.AcceptFindings {
		// Nothing is saved until the clinician has seen what the checks
		// found and saves the value all the same.
		writeJSON(w, http.StatusUnprocessableEntity, map[string]any{
			"error":    "the corrected value fails a check; save it again to keep it",
			"findings": applied.Findings,
		})
		return
	}

	findings := make([]string, 0, len(applied.Findings))
	for _, f := range applied.Findings {
		findings = append(findings, f.Validator)
	}
	action := actFieldConfirmed
	var corrected *string
	if req.Action == "correct" {
		action = actFieldCorrected
		v := strings.TrimSpace(*req.Value)
		corrected = &v
	}
	verified := applied.Flagged == 0
	details := map[string]any{"item_id": req.ItemID, "signals": applied.Signals, "verified": verified}
	if req.Field != nil {
		details["field"] = *req.Field
	}
	if len(findings) > 0 {
		details["findings_accepted"] = findings
	}
	if len(req.Acknowledge) > 0 {
		details["acknowledged"] = req.Acknowledge
	}
	ev := with(s.documentEvent(r, action, doc), details)

	updated, err := s.db.ApplyReview(ctx, p.OrgID, docID, rec.UpdatedAt, db.RecordReview{
		Record: applied.Record, HTML: applied.HTML, Verified: verified,
		RunID: rec.RunID, ItemID: req.ItemID, Field: req.Field, Action: req.Action,
		Original: applied.Original, Corrected: corrected, Note: trimmedOrNil(req.Note),
		Signals: applied.Signals, Findings: findings, ReviewerID: p.UserID, At: now,
	}, ev)
	if errors.Is(err, db.ErrReviewConflict) {
		http.Error(w, "the record changed since you opened it; reload the page", http.StatusConflict)
		return
	}
	if err != nil {
		log.Printf("ApplyReview error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	newStatus := ins.Status
	if verified {
		newStatus = "verified"
		// The summary still shows the values as they were read: rebuild
		// it. The review stands if that fails.
		if err := s.requestPatientSummary(r, doc.PatientID); err != nil {
			log.Printf("could not rebuild the summary after reviewing document %s: %v", docID, err)
		}
	}
	writeJSON(w, http.StatusOK, reviewResponse{
		DocumentID: docID, Status: newStatus, RecordUpdatedAt: updated,
		Flagged: applied.Flagged, Findings: applied.Findings,
	})
}

// ----- POST /documents/{id}/audit-complete -----

// handleCompleteAudit finishes the audit of a verified document sampled
// for one, once the clinician has checked it (confirming or correcting
// values as in any review). The document leaves the queue; whether the
// audit found an error comes from its reviews. See
// docs/adr/0011-clinician-review.md.
func (s *Server) handleCompleteAudit(w http.ResponseWriter, r *http.Request, docID string) {
	p, _ := principalFromContext(r.Context())
	doc := s.loadDocument(w, r, docID)
	if doc == nil {
		return
	}
	now := s.now().UTC()
	err := s.db.CompleteAudit(r.Context(), p.OrgID, docID, p.UserID, now,
		s.documentEvent(r, actAuditCompleted, doc))
	if errors.Is(err, db.ErrNoOpenAudit) {
		http.Error(w, "this document is not waiting for an audit", http.StatusConflict)
		return
	}
	var incomplete *db.AuditIncompleteError
	if errors.As(err, &incomplete) {
		http.Error(w, fmt.Sprintf("confirm or correct every value before finishing the audit: %d to go",
			incomplete.Unreviewed), http.StatusConflict)
		return
	}
	if err != nil {
		log.Printf("CompleteAudit error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"document_id": docID, "completed_at": now})
}

// problem says what is wrong with the request's shape, or "".
func (req *reviewRequest) problem() string {
	if !idPattern(req.ItemID) {
		return "item_id must name an item, the patient or the document"
	}
	if req.RecordUpdatedAt.IsZero() {
		return "record_updated_at is required"
	}
	if req.Note != nil && len(*req.Note) > maxReviewNote {
		return fmt.Sprintf("the note is longer than %d characters", maxReviewNote)
	}
	if len(req.Acknowledge) > maxAcknowledged {
		return fmt.Sprintf("at most %d signals can be acknowledged", maxAcknowledged)
	}
	for _, id := range req.Acknowledge {
		if !signalPattern(id) {
			return "acknowledge must list signal ids such as LC-13"
		}
	}
	switch req.Action {
	case "confirm":
		if req.Field != nil || req.Value != nil {
			return "a confirmation is of the whole item: no field or value"
		}
	case "correct":
		if req.Field == nil || req.Value == nil || strings.TrimSpace(*req.Value) == "" {
			return "a correction needs a field and a value"
		}
		if len(*req.Value) > maxReviewValue {
			return fmt.Sprintf("the value is longer than %d characters", maxReviewValue)
		}
	default:
		return "action must be confirm or correct"
	}
	return ""
}

// acknowledged is the acknowledged signal ids, never null.
func (req *reviewRequest) acknowledged() []string {
	if req.Acknowledge == nil {
		return []string{}
	}
	return req.Acknowledge
}

// signalPattern accepts a routing signal id: LC- and two digits.
func signalPattern(id string) bool {
	if len(id) != 5 || !strings.HasPrefix(id, "LC-") {
		return false
	}
	return id[3] >= '0' && id[3] <= '9' && id[4] >= '0' && id[4] <= '9'
}

// idPattern accepts what record item ids look like: patient, document,
// or a group prefix and a number (obs-3).
func idPattern(id string) bool {
	if id == "" || len(id) > 32 {
		return false
	}
	for i, c := range id {
		switch {
		case c >= 'a' && c <= 'z':
		case c >= '0' && c <= '9' && i > 0:
		case c == '-' && i > 0:
		default:
			return false
		}
	}
	return true
}

func trimmedOrNil(s *string) *string {
	if s == nil {
		return nil
	}
	t := strings.TrimSpace(*s)
	if t == "" {
		return nil
	}
	return &t
}

// callInsightsServiceJSON POSTs payload to an internal AI service route
// that answers with JSON, and decodes a 200 answer into out. A 422 is
// returned as its status and the service's detail text (not an error);
// any other status is an error.
func (s *Server) callInsightsServiceJSON(ctx context.Context, path string, payload, out any) (int, string, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return 0, "", err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.insightsURL+path, bytes.NewReader(body))
	if err != nil {
		return 0, "", err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Internal-Token", s.internalToken)
	resp, err := s.httpClient.Do(req)
	if err != nil {
		return 0, "", err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if err != nil {
		return 0, "", err
	}
	switch resp.StatusCode {
	case http.StatusOK:
		return http.StatusOK, "", json.Unmarshal(raw, out)
	case http.StatusUnprocessableEntity:
		var e struct {
			Detail any `json:"detail"`
		}
		_ = json.Unmarshal(raw, &e)
		if d, ok := e.Detail.(string); ok && d != "" {
			return resp.StatusCode, d, nil
		}
		return resp.StatusCode, "the review could not be applied", nil
	default:
		return 0, "", fmt.Errorf("insights service returned %s", resp.Status)
	}
}
