package httpapi

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"backend/internal/auth"
	"backend/internal/authz"
	"backend/internal/db"

	"github.com/jackc/pgx/v5/pgtype"
)

// Store is the data access the handlers use. *db.Store implements it; tests
// use an in-memory fake.
type Store interface {
	// Accounts and sessions
	GetUserByEmail(ctx context.Context, email string) (*db.User, error)
	GetUserByID(ctx context.Context, id string) (*db.User, error)
	RecordLoginFailure(ctx context.Context, userID string, maxFailures int, lockUntil time.Time, ev *db.AuditEvent) (*time.Time, error)
	RecordLoginSuccess(ctx context.Context, userID string, now time.Time, ev *db.AuditEvent) error
	SetPasswordHash(ctx context.Context, userID, hash string, now time.Time, ev *db.AuditEvent) error
	CreateSession(ctx context.Context, sess db.Session) error
	GetSession(ctx context.Context, tokenHash []byte) (*db.SessionWithUser, error)
	TouchSession(ctx context.Context, tokenHash []byte, now time.Time) error
	DeleteSession(ctx context.Context, tokenHash []byte, ev *db.AuditEvent) error
	DeleteUserSessions(ctx context.Context, userID string, keep []byte, ev *db.AuditEvent) error
	DeleteExpiredSessions(ctx context.Context, now time.Time, idle time.Duration) (int64, error)
	ListUserSessions(ctx context.Context, userID string) ([]db.Session, error)
	DeleteUserSession(ctx context.Context, userID, sessionID string, ev *db.AuditEvent) (bool, error)
	GetUsableToken(ctx context.Context, tokenHash []byte, now time.Time) (*db.User, error)
	CompletePasswordSetup(ctx context.Context, tokenHash []byte, passwordHash string, now time.Time, ev *db.AuditEvent) (string, error)
	CreateOneTimeToken(ctx context.Context, t db.OneTimeToken, ev *db.AuditEvent) error

	// Staff management, always within one organisation
	ListUsers(ctx context.Context, orgID string) ([]db.User, error)
	CreateUser(ctx context.Context, nu db.NewUser, ev *db.AuditEvent) (*db.User, error)
	UpdateUser(ctx context.Context, upd db.UserUpdate, ev *db.AuditEvent) (*db.User, error)

	// The audit log. A method that changes something takes the event to
	// write in its own transaction (nil only on maintenance paths);
	// RecordAudit writes one on its own, for reads.
	RecordAudit(ctx context.Context, ev db.AuditEvent) error
	ListAuditEvents(ctx context.Context, orgID string, f db.AuditFilter) ([]db.AuditEvent, error)

	// Patients, documents and summaries. Every method that finds a patient
	// or a document takes the caller's organisation and filters on it.
	CreatePatient(ctx context.Context, orgID, createdBy string, f db.PatientFields, ev *db.AuditEvent) (*db.Patient, error)
	UpdatePatient(ctx context.Context, orgID, patientID string, f db.PatientFields, ev *db.AuditEvent) (*db.Patient, error)
	GetPatient(ctx context.Context, orgID, patientID string) (*db.Patient, error)
	ListPatients(ctx context.Context, orgID, query string) ([]db.Patient, error)
	GetPatientSummary(ctx context.Context, patientID string) (*db.PatientSummary, error)
	CreateDocument(ctx context.Context, orgID string, nd db.NewDocument, ev *db.AuditEvent) (*db.Document, error)
	GetDocument(ctx context.Context, orgID, docID string) (*db.Document, error)
	ListPatientDocuments(ctx context.Context, orgID, patientID string) ([]db.Document, error)
	SoftDeleteDocument(ctx context.Context, orgID, docID, deletedBy string, now time.Time, ev *db.AuditEvent) (bool, error)
	GetInsightByDocumentID(ctx context.Context, docID string) (*db.Insight, error)
	GetDocumentRecord(ctx context.Context, docID string) (*db.DocumentRecord, error)

	GetProcessing(ctx context.Context, docID string) (*db.ProcessingRun, error)
	GetDocumentTrace(ctx context.Context, docID, runID string) (*db.DocumentTrace, bool, error)
	ListOrgRuns(ctx context.Context, orgID string, before *time.Time) ([]db.OrgRun, error)
	GetOrgRun(ctx context.Context, orgID, runID string) (*db.OrgRun, []db.OrgStep, error)

	// Clinician review (review.go)
	ListReviewQueue(ctx context.Context, orgID string) ([]db.ReviewQueueEntry, error)
	ApplyReview(ctx context.Context, orgID, docID string, recordUpdatedAt time.Time, rv db.RecordReview, ev *db.AuditEvent) (time.Time, error)
	CompleteAudit(ctx context.Context, orgID, docID, userID string, at time.Time, ev *db.AuditEvent) error
}

// Config is what the server needs besides its store.
type Config struct {
	UploadsDir     string   // absolute, so the AI service can open stored paths
	InsightsURL    string   // AI service base URL
	InternalToken  string   // shared secret the AI service requires
	AllowedOrigins []string // browser origins allowed to call the API
	CookieSecure   bool     // false only for plain-HTTP local development
	AppURL         string   // where the frontend is served; setup links point there
	Hasher         *auth.Hasher
	Now            func() time.Time // the clock; time.Now when nil
}

type Server struct {
	db             Store
	uploadsDir     string
	insightsURL    string
	internalToken  string
	allowedOrigins map[string]bool
	cookieSecure   bool
	appURL         string
	hasher         *auth.Hasher
	now            func() time.Time
	httpClient     *http.Client
	mux            *http.ServeMux
	handler        http.Handler
	authLimiter    *rateLimiter
}

func NewServer(store Store, cfg Config) *Server {
	s := &Server{
		db:             store,
		uploadsDir:     cfg.UploadsDir,
		insightsURL:    strings.TrimRight(cfg.InsightsURL, "/"),
		internalToken:  cfg.InternalToken,
		allowedOrigins: map[string]bool{},
		cookieSecure:   cfg.CookieSecure,
		appURL:         strings.TrimRight(cfg.AppURL, "/"),
		hasher:         cfg.Hasher,
		now:            cfg.Now,
		httpClient:     &http.Client{Timeout: 10 * time.Second},
		mux:            http.NewServeMux(),
		authLimiter:    newRateLimiter(authAttemptsPerWindow, authWindow),
	}
	if s.now == nil {
		s.now = time.Now
	}
	for _, o := range cfg.AllowedOrigins {
		s.allowedOrigins[o] = true
	}

	// Public
	s.mux.HandleFunc("/healthz", s.handleHealth)
	s.mux.HandleFunc("/auth/login", s.handleLogin)
	s.mux.HandleFunc("/auth/logout", s.handleLogout)
	s.mux.HandleFunc("/auth/setup/info", s.handleSetupInfo)
	s.mux.HandleFunc("/auth/setup", s.handleSetup)

	// Logged in
	s.mux.HandleFunc("/auth/me", s.withSession(s.handleMe))
	s.mux.HandleFunc("/auth/password", s.withSession(s.handleChangePassword))
	// /auth/sessions (GET = list mine, DELETE = end all but this one) and
	// /auth/sessions/{id} (DELETE = end one of mine)
	s.mux.HandleFunc("/auth/sessions", s.withSession(s.handleSessions))
	s.mux.HandleFunc("/auth/sessions/", s.withSession(s.handleSessionByID))

	// Staff management (admins)
	s.mux.HandleFunc("/admin/users", s.withSession(s.byMethod(map[string]methodRoute{
		http.MethodGet:  {authz.StaffManage, s.handleListStaff},
		http.MethodPost: {authz.StaffManage, s.handleCreateStaff},
	})))
	s.mux.HandleFunc("/admin/users/", s.withSession(s.handleStaffByID))
	s.mux.HandleFunc("/admin/audit", s.withSession(s.byMethod(map[string]methodRoute{
		http.MethodGet: {authz.AuditRead, s.handleListAudit},
	})))
	// The organisation's pipeline runs without content (trace.go)
	s.mux.HandleFunc("/admin/runs", s.withSession(s.byMethod(map[string]methodRoute{
		http.MethodGet: {authz.RunsRead, s.handleListRuns},
	})))
	s.mux.HandleFunc("/admin/runs/", s.withSession(s.handleRunByID))

	// Patients (patients.go): /patients, /patients/{id}, and the patient's
	// /documents (list, upload) and /summary (fetch, build)
	s.mux.HandleFunc("/patients", s.withSession(s.byMethod(map[string]methodRoute{
		http.MethodGet:  {authz.PatientRead, s.handleListPatients},
		http.MethodPost: {authz.PatientWrite, s.handleCreatePatient},
	})))
	s.mux.HandleFunc("/patients/", s.withSession(s.handlePatientByID))
	// /documents/{id} (GET, DELETE) and its /insight, /record, /reprocess, /review
	s.mux.HandleFunc("/documents/", s.withSession(s.handleDocumentByID))
	// The documents waiting for a clinician's review (review.go)
	s.mux.HandleFunc("/review/queue", s.withSession(s.byMethod(map[string]methodRoute{
		http.MethodGet: {authz.ReviewWrite, s.handleReviewQueue},
	})))

	s.handler = s.withSecurityHeaders(s.withOrigins(s.withAuthRateLimit(s.mux)))
	return s
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.handler.ServeHTTP(w, r)
}

// Simple helper for JSON responses
func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if v != nil {
		_ = json.NewEncoder(w).Encode(v)
	}
}

// ============
// Handlers
// ============

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ok"))
}

// ----- Upload: POST /patients/{id}/documents -----

// maxUploadSize caps a whole upload request (the file plus form overhead).
const maxUploadSize = 20 << 20 // 20 MB

// handleUploadDocument stores a file as a document of the patient, uploaded
// by the caller, and starts its processing.
func (s *Server) handleUploadDocument(w http.ResponseWriter, r *http.Request, patientID string) {
	p, _ := principalFromContext(r.Context())
	if s.loadPatient(w, r, patientID) == nil {
		return
	}

	// ParseMultipartForm's argument only limits memory and spills the rest
	// to temp files; MaxBytesReader is what caps the request size.
	r.Body = http.MaxBytesReader(w, r.Body, maxUploadSize)
	if err := r.ParseMultipartForm(maxUploadSize); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			http.Error(w, "file too large (max 20 MB)", http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, "failed to parse form", http.StatusBadRequest)
		return
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		http.Error(w, "file field is required", http.StatusBadRequest)
		return
	}
	defer file.Close()

	if err := checkFileType(file, header.Filename); err != nil {
		http.Error(w, err.Error(), http.StatusUnsupportedMediaType)
		return
	}

	// Store under a random name: original names collide across users, and a
	// collision would point one patient's document at another's file.
	storagePath, err := newStoragePath(s.uploadsDir, header.Filename)
	if err != nil {
		log.Printf("newStoragePath error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	dst, err := os.OpenFile(storagePath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
	if err != nil {
		http.Error(w, "failed to save file", http.StatusInternalServerError)
		return
	}
	// Hashed as it is written, so an exact copy of an earlier upload can be
	// flagged.
	hash := sha256.New()
	_, err = dst.ReadFrom(io.TeeReader(file, hash))
	if closeErr := dst.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		_ = os.Remove(storagePath)
		http.Error(w, "failed to write file", http.StatusInternalServerError)
		return
	}

	doc, err := s.db.CreateDocument(r.Context(), p.OrgID, db.NewDocument{
		PatientID:    patientID,
		UploadedBy:   p.UserID,
		OriginalName: header.Filename,
		ContentType:  header.Header.Get("Content-Type"),
		StoragePath:  storagePath,
		SHA256:       hex.EncodeToString(hash.Sum(nil)),
	}, forPatient(on(s.auditEvent(r, actDocumentUploaded), targetDocument, ""), patientID))
	if err != nil || doc == nil {
		_ = os.Remove(storagePath)
		if err != nil {
			log.Printf("CreateDocument error: %v", err)
			http.Error(w, "internal error", http.StatusInternalServerError)
		} else {
			http.Error(w, "not found", http.StatusNotFound) // the patient went away meanwhile
		}
		return
	}

	// Start processing now instead of when the document is first opened. The
	// upload still succeeds if the AI service is down: opening the document
	// requests processing again.
	if err := s.requestInsight(r.Context(), doc.ID); err != nil {
		log.Printf("could not start processing for document %s: %v", doc.ID, err)
	}

	writeJSON(w, http.StatusCreated, doc)
}

// allowedTypes maps the upload extensions the AI service can read to the
// content type their first bytes must have.
var allowedTypes = map[string]string{
	".pdf":  "application/pdf",
	".jpg":  "image/jpeg",
	".jpeg": "image/jpeg",
	".png":  "image/png",
}

// checkFileType accepts PDF, JPEG and PNG files whose content matches their
// extension, then rewinds the file for saving.
func checkFileType(file multipart.File, name string) error {
	want, ok := allowedTypes[strings.ToLower(filepath.Ext(name))]
	if !ok {
		return errors.New("unsupported file type: upload a PDF, JPG or PNG")
	}
	head := make([]byte, 512)
	n, err := io.ReadFull(file, head)
	if err != nil && !errors.Is(err, io.ErrUnexpectedEOF) {
		return errors.New("could not read the uploaded file")
	}
	if http.DetectContentType(head[:n]) != want {
		return errors.New("file content does not match its extension")
	}
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return errors.New("could not read the uploaded file")
	}
	return nil
}

// newStoragePath returns a fresh random path in dir. It keeps the upload's
// extension because the AI service picks image vs PDF handling by it.
func newStoragePath(dir, originalName string) (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	name := hex.EncodeToString(b) + strings.ToLower(filepath.Ext(originalName))
	return filepath.Join(dir, name), nil
}

// ----- Single document routes: /documents/{id} and /documents/{id}/insight -----

func (s *Server) handleDocumentByID(w http.ResponseWriter, r *http.Request) {
	// Path is /documents/{id}, /documents/{id}/insight, /documents/{id}/record
	// or /documents/{id}/reprocess
	path := strings.TrimPrefix(r.URL.Path, "/documents/")
	if path == "" {
		w.WriteHeader(http.StatusNotFound)
		return
	}

	parts := strings.Split(path, "/")
	docID := parts[0]

	// Reject malformed IDs here; Postgres would fail on the uuid cast (500).
	var parsed pgtype.UUID
	if err := parsed.Scan(docID); err != nil {
		http.Error(w, "invalid document id", http.StatusBadRequest)
		return
	}

	// /documents/{id}/insight
	if len(parts) == 2 && parts[1] == "insight" {
		switch r.Method {
		case http.MethodGet:
			if s.permit(w, r, authz.DocumentRead) {
				s.handleGetDocumentInsight(w, r, docID)
			}
		case http.MethodPost:
			if s.permit(w, r, authz.DocumentReprocess) {
				s.handleGenerateDocumentInsight(w, r, docID)
			}
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
		return
	}

	// /documents/{id}/record: the structured record with its citations
	if len(parts) == 2 && parts[1] == "record" {
		if r.Method != http.MethodGet {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		if s.permit(w, r, authz.DocumentRead) {
			s.handleGetDocumentRecord(w, r, docID)
		}
		return
	}

	// /documents/{id}/processing: how the latest run went, step by step
	if len(parts) == 2 && parts[1] == "processing" {
		if r.Method != http.MethodGet {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		if s.permit(w, r, authz.DocumentRead) {
			s.handleGetProcessing(w, r, docID)
		}
		return
	}

	// /documents/{id}/trace: every run, and one in full (trace.go)
	if len(parts) == 2 && parts[1] == "trace" {
		if r.Method != http.MethodGet {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		if s.permit(w, r, authz.DocumentRead) {
			s.handleGetTrace(w, r, docID)
		}
		return
	}

	// /documents/{id}/file: the original upload, for checking values against
	if len(parts) == 2 && parts[1] == "file" {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		if s.permit(w, r, authz.DocumentRead) {
			s.handleGetDocumentFile(w, r, docID)
		}
		return
	}

	// /documents/{id}/review: a clinician confirms or corrects a value
	if len(parts) == 2 && parts[1] == "review" {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		if s.permit(w, r, authz.ReviewWrite) {
			s.handleReviewDocument(w, r, docID)
		}
		return
	}

	// /documents/{id}/audit-complete: a clinician finishes an audit sample
	if len(parts) == 2 && parts[1] == "audit-complete" {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		if s.permit(w, r, authz.ReviewWrite) {
			s.handleCompleteAudit(w, r, docID)
		}
		return
	}

	// /documents/{id}/reprocess: process again, from the start, as a new run
	if len(parts) == 2 && parts[1] == "reprocess" {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		if s.permit(w, r, authz.DocumentReprocess) {
			s.handleReprocessDocument(w, r, docID)
		}
		return
	}

	// /documents/{id} only (no further segments)
	if len(parts) > 1 {
		w.WriteHeader(http.StatusNotFound)
		return
	}

	switch r.Method {
	case http.MethodGet:
		if s.permit(w, r, authz.DocumentRead) {
			if doc := s.loadDocument(w, r, docID); doc != nil &&
				s.recordAudit(w, r, s.documentEvent(r, actDocumentViewed, doc)) {
				writeJSON(w, http.StatusOK, doc)
			}
		}
	case http.MethodDelete:
		if s.permit(w, r, authz.DocumentDelete) {
			s.handleDeleteDocument(w, r, docID)
		}
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// loadDocument returns a live document of the caller's organisation, or
// answers 404 (or 500) and returns nil. Every document route starts here,
// so a document of another organisation is never found.
func (s *Server) loadDocument(w http.ResponseWriter, r *http.Request, docID string) *db.Document {
	p, _ := principalFromContext(r.Context())
	doc, err := s.db.GetDocument(r.Context(), p.OrgID, docID)
	if err != nil {
		log.Printf("GetDocument error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return nil
	}
	if doc == nil {
		http.Error(w, "not found", http.StatusNotFound)
		return nil
	}
	return doc
}

// handleDeleteDocument deletes a document, softly: it is hidden from every
// list, page and summary, while its row and file stay so the deletion
// stays accountable. Only the clinician who uploaded it may delete it.
// See docs/adr/0010-staff-access-and-security.md.
func (s *Server) handleDeleteDocument(w http.ResponseWriter, r *http.Request, docID string) {
	p, _ := principalFromContext(r.Context())
	doc := s.loadDocument(w, r, docID)
	if doc == nil {
		return
	}
	if doc.UploadedBy == nil || *doc.UploadedBy != p.UserID {
		ev := with(s.documentEvent(r, actDocumentDeleted, doc), map[string]any{"reason": "not_uploader"})
		ev.Outcome = db.OutcomeDenied
		if s.recordAudit(w, r, ev) {
			http.Error(w, "only the clinician who uploaded a document can delete it", http.StatusForbidden)
		}
		return
	}
	ok, err := s.db.SoftDeleteDocument(r.Context(), p.OrgID, docID, p.UserID, s.now(),
		s.documentEvent(r, actDocumentDeleted, doc))
	if err != nil {
		log.Printf("SoftDeleteDocument error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if !ok {
		http.Error(w, "not found", http.StatusNotFound) // deleted meanwhile
		return
	}
	// The patient summary still shows the document's values: rebuild it
	// if the patient has one. The deletion stands if that fails.
	summary, err := s.db.GetPatientSummary(r.Context(), doc.PatientID)
	if err == nil && summary.Status != nil {
		if err := s.requestPatientSummary(r, doc.PatientID); err != nil {
			log.Printf("could not rebuild the summary after deleting document %s: %v", docID, err)
		}
	}
	w.WriteHeader(http.StatusNoContent)
}

// ----- Get insights for a document: GET /documents/{id}/insight -----

type insightResponse struct {
	DocumentID   string  `json:"document_id"`
	Status       string  `json:"status"`                  // "not_started" | "processing" | "verified" | "needs_review" | "failed"
	InsightsHTML *string `json:"insights_html,omitempty"` // present when verified or needs_review
	ErrorMessage *string `json:"error_message,omitempty"` // present when failed
}

func (s *Server) handleGetDocumentInsight(w http.ResponseWriter, r *http.Request, docID string) {
	ctx := r.Context()
	doc := s.loadDocument(w, r, docID)
	if doc == nil {
		return
	}

	// 2) Try to fetch insight for this document
	ins, err := s.db.GetInsightByDocumentID(ctx, docID)
	if err != nil {
		log.Printf("GetInsightByDocumentID error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	// No insight row: no job has claimed this document yet. The client
	// requests processing (POST); a request that races a queued job is
	// harmless because the AI service claims each document once.
	if !s.recordAudit(w, r, s.documentEvent(r, actInsightViewed, doc)) {
		return
	}
	if ins == nil {
		resp := insightResponse{
			DocumentID: docID,
			Status:     "not_started",
		}
		writeJSON(w, http.StatusOK, resp)
		return
	}

	// Map DB insight fields to API response
	resp := insightResponse{
		DocumentID:   docID,
		Status:       ins.Status, // e.g. "verified", "failed"
		InsightsHTML: nil,
		ErrorMessage: nil,
	}

	if insightEnded(ins.Status) {
		resp.InsightsHTML = &ins.HTMLInsights
	} else if ins.Status == "failed" && ins.ErrorMessage != nil {
		resp.ErrorMessage = ins.ErrorMessage
	}

	writeJSON(w, http.StatusOK, resp)
}

// insightEnded reports whether a document's processing reached an end
// state with a result to show: verified, or needs_review (some values
// await a clinician's review; the page is shown with that warning).
func insightEnded(status string) bool {
	return status == "verified" || status == "needs_review"
}

// ----- Start insight generation: POST /documents/{id}/insight -----

// The AI service trusts whatever document ID it is given, so the browser
// never calls it directly: requests come through here, where the organisation is
// checked, and carry the shared internal token.
func (s *Server) handleGenerateDocumentInsight(w http.ResponseWriter, r *http.Request, docID string) {
	doc := s.loadDocument(w, r, docID)
	if doc == nil {
		return
	}
	// Recorded before the AI service is asked, as it writes no audit row.
	if !s.recordAudit(w, r, s.documentEvent(r, actInsightRequested, doc)) {
		return
	}

	if err := s.requestInsight(r.Context(), docID); err != nil {
		log.Printf("insights service error: %v", err)
		http.Error(w, "insights service unavailable", http.StatusBadGateway)
		return
	}

	writeJSON(w, http.StatusAccepted, map[string]string{"status": "accepted", "document_id": docID})
}

// ----- Reprocess: POST /documents/{id}/reprocess -----

// Processing again replaces the document's record with a new run's, for
// when the pipeline has changed. A document with a job already queued or
// running is left to it.
func (s *Server) handleReprocessDocument(w http.ResponseWriter, r *http.Request, docID string) {
	doc := s.loadDocument(w, r, docID)
	if doc == nil {
		return
	}
	if !s.recordAudit(w, r, s.documentEvent(r, actDocumentReprocess, doc)) {
		return
	}
	err := s.callInsightsService(r.Context(), "/internal/reprocess-document", map[string]string{"document_id": docID})
	if err != nil {
		log.Printf("insights service error: %v", err)
		http.Error(w, "insights service unavailable", http.StatusBadGateway)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]string{"status": "accepted", "document_id": docID})
}

// documentEvent is an audit event about one document, and so its patient.
func (s *Server) documentEvent(r *http.Request, action string, doc *db.Document) *db.AuditEvent {
	return forPatient(on(s.auditEvent(r, action), targetDocument, doc.ID), doc.PatientID)
}

// requestInsight asks the AI service to process a document. The AI service
// acts on any document ID it is given, so callers load the document first (loadDocument).
func (s *Server) requestInsight(ctx context.Context, docID string) error {
	return s.callInsightsService(ctx, "/internal/generate-insights", map[string]string{"document_id": docID})
}

// callInsightsService POSTs payload as JSON to an internal AI service route,
// with the shared token. The AI service queues the work and replies at once.
func (s *Server) callInsightsService(ctx context.Context, path string, payload any) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.insightsURL+path, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Internal-Token", s.internalToken)

	resp, err := s.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("insights service returned %s", resp.Status)
	}
	return nil
}
