package httpapi

import (
	"log"
	"net/http"
	"strconv"
	"time"

	"backend/internal/db"
)

// Audit actions. Each names what happened, not the route, so the log
// reads the same whichever client caused it.
// See docs/adr/0010-staff-access-and-security.md.
const (
	actLoginSucceeded  = "login_succeeded"
	actLoginFailed     = "login_failed"
	actAccountLocked   = "account_locked"
	actLogout          = "logout"
	actPasswordChanged = "password_changed"
	actPasswordSet     = "password_set" // through a setup or reset link
	actSessionEnded    = "session_ended"
	actSessionsEnded   = "other_sessions_ended"
	actStaffInvited    = "staff_invited"
	actStaffUpdated    = "staff_updated"
	actLinkIssued      = "link_issued"
	actAccessDenied    = "access_denied"
	actRateLimited     = "rate_limited"

	actPatientsListed     = "patients_listed"
	actPatientCreated     = "patient_created"
	actPatientViewed      = "patient_viewed"
	actPatientUpdated     = "patient_updated"
	actDocumentsListed    = "patient_documents_listed"
	actDocumentUploaded   = "document_uploaded"
	actDocumentViewed     = "document_viewed"
	actDocumentDeleted    = "document_deleted"
	actInsightViewed      = "insight_viewed"
	actInsightRequested   = "insight_requested"
	actDocumentReprocess  = "document_reprocessed"
	actRecordViewed       = "record_viewed"
	actSummaryViewed      = "summary_viewed"
	actSummaryRequested   = "summary_requested"
	actAuditViewed        = "audit_viewed"
	targetPatient         = "patient"
	targetDocument        = "document"
	targetStaff           = "staff_user"
	targetSession         = "session"
	targetAudit           = "audit_log"
	auditUserAgentMaxSize = 512
)

func optStr(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

// toolHeader names the tool of the MCP server a request is made for. The
// server sends it with every request, so each tool call's audit row says
// which tool read the data. It is a label, not a credential: the actor is
// still the session's account. See docs/adr/0012-shared-tool-layer.md.
const toolHeader = "X-Medsum-Tool"

// toolName is the request's tool label, or "" when it has none or the
// header isn't a plain tool name (lower case and underscores).
func toolName(r *http.Request) string {
	v := r.Header.Get(toolHeader)
	if v == "" || len(v) > 40 {
		return ""
	}
	for _, c := range v {
		if (c < 'a' || c > 'z') && c != '_' {
			return ""
		}
	}
	return v
}

// auditEvent starts an event for the request: the caller (when logged in),
// their organisation, the address and browser, the MCP tool it is made for
// (if any), and the time. Callers add the target, the patient and any
// details.
func (s *Server) auditEvent(r *http.Request, action string) *db.AuditEvent {
	ev := &db.AuditEvent{
		At:        s.now(),
		Action:    action,
		Outcome:   db.OutcomeSuccess,
		IP:        s.clientIP(r),
		UserAgent: truncate(r.UserAgent(), auditUserAgentMaxSize),
	}
	if p, ok := principalFromContext(r.Context()); ok {
		ev.ActorID = optStr(p.UserID)
		ev.OrgID = optStr(p.OrgID)
	}
	if tool := toolName(r); tool != "" {
		ev.Details = map[string]any{"tool": tool}
	}
	return ev
}

// on sets the event's target and returns it.
func on(ev *db.AuditEvent, targetType, targetID string) *db.AuditEvent {
	ev.TargetType = optStr(targetType)
	ev.TargetID = optStr(targetID)
	return ev
}

// forPatient sets the patient the event concerns and returns it.
func forPatient(ev *db.AuditEvent, patientID string) *db.AuditEvent {
	ev.PatientID = optStr(patientID)
	return ev
}

// with adds details (counts, field names, reasons; never content) to
// those the event has.
func with(ev *db.AuditEvent, details map[string]any) *db.AuditEvent {
	if ev.Details == nil {
		ev.Details = make(map[string]any, len(details))
	}
	for k, v := range details {
		ev.Details[k] = v
	}
	return ev
}

// recordAudit writes an event on its own, before the response, and
// answers 500 when it can't: a read that can't be recorded is refused,
// so nothing is seen unrecorded. It reports whether the caller may go on.
func (s *Server) recordAudit(w http.ResponseWriter, r *http.Request, ev *db.AuditEvent) bool {
	if err := s.db.RecordAudit(r.Context(), *ev); err != nil {
		log.Printf("RecordAudit error (action %s): %v", ev.Action, err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return false
	}
	return true
}

// ----- GET /admin/audit -----

type auditPage struct {
	Events     []db.AuditEvent `json:"events"`
	NextBefore *int64          `json:"next_before"` // pass as ?before= for older entries
}

// handleListAudit returns the organisation's audit log, newest first,
// filtered by ?actor=, ?patient=, ?from= and ?to= (UTC dates, YYYY-MM-DD,
// both inclusive, or times, RFC 3339, the last exclusive: the page sends
// the days of its own time zone as times) and paged with ?before=.
func (s *Server) handleListAudit(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	f := db.AuditFilter{ActorID: q.Get("actor"), PatientID: q.Get("patient")}
	for _, id := range []string{f.ActorID, f.PatientID} {
		if id != "" && !validUUID(id) {
			http.Error(w, "actor and patient must be ids", http.StatusBadRequest)
			return
		}
	}
	for name, dst := range map[string]**time.Time{"from": &f.From, "to": &f.To} {
		v := q.Get(name)
		if v == "" {
			continue
		}
		if at, err := time.Parse(time.RFC3339, v); err == nil {
			*dst = &at
			continue
		}
		day, err := time.Parse(time.DateOnly, v)
		if err != nil {
			http.Error(w, name+" must be a date, YYYY-MM-DD, or a time, RFC 3339", http.StatusBadRequest)
			return
		}
		if name == "to" {
			day = day.AddDate(0, 0, 1) // the whole of the last day
		}
		*dst = &day
	}
	if v := q.Get("before"); v != "" {
		id, err := strconv.ParseInt(v, 10, 64)
		if err != nil || id <= 0 {
			http.Error(w, "before must be an entry id", http.StatusBadRequest)
			return
		}
		f.BeforeID = id
	}
	f.Limit = db.MaxAuditPage

	p, _ := principalFromContext(r.Context())
	events, err := s.db.ListAuditEvents(r.Context(), p.OrgID, f)
	if err != nil {
		log.Printf("ListAuditEvents error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if !s.recordAudit(w, r, with(on(s.auditEvent(r, actAuditViewed), targetAudit, ""),
		map[string]any{"entries": len(events)})) {
		return
	}
	page := auditPage{Events: events}
	if page.Events == nil {
		page.Events = []db.AuditEvent{}
	}
	if len(events) == f.Limit {
		last := events[len(events)-1].ID
		page.NextBefore = &last
	}
	writeJSON(w, http.StatusOK, page)
}
