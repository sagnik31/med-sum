package httpapi

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"backend/internal/db"
)

type Server struct {
	db        *db.Store
	jwtSecret []byte
	mux       *http.ServeMux
}

func NewServer(store *db.Store, jwtSecret string) *Server {
	s := &Server{
		db:        store,
		jwtSecret: []byte(jwtSecret),
		mux:       http.NewServeMux(),
	}

	// Public
	s.mux.HandleFunc("/healthz", s.handleHealth)
	s.mux.HandleFunc("/login", s.handleLogin)

	// Authenticated:
	// /documents (POST = upload, GET = list)
	s.mux.HandleFunc("/documents", s.withAuth(s.handleDocuments))
	// /documents/{id} and /documents/{id}/insight
	s.mux.HandleFunc("/documents/", s.withAuth(s.handleDocumentByID))

	// /user/insights
	s.mux.HandleFunc("/user/insights", s.withAuth(s.handleGetPatientInsights))

	return s
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.mux.ServeHTTP(w, r)
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

// ----- Login -----

type loginRequest struct {
	PhoneNumber string  `json:"phone_number"`
	FullName    *string `json:"full_name"`
}

type loginResponse struct {
	Token string  `json:"token"`
	User  db.User `json:"user"`
}

func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}

	var req loginRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid JSON body", http.StatusBadRequest)
		return
	}
	if strings.TrimSpace(req.PhoneNumber) == "" {
		http.Error(w, "phone_number is required", http.StatusBadRequest)
		return
	}

	user, err := s.db.UpsertUserByPhone(r.Context(), req.PhoneNumber, req.FullName)
	if err != nil {
		log.Printf("UpsertUserByPhone error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	token, err := s.generateToken(user.ID)
	if err != nil {
		log.Printf("generateToken error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	resp := loginResponse{
		Token: token,
		User:  *user,
	}
	writeJSON(w, http.StatusOK, resp)
}

// ----- Documents collection: POST /documents, GET /documents -----

func (s *Server) handleDocuments(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodPost:
		s.handleUploadDocument(w, r)
	case http.MethodGet:
		s.handleListDocuments(w, r)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// Upload document for the authenticated user.
func (s *Server) handleUploadDocument(w http.ResponseWriter, r *http.Request) {
	userID, ok := userIDFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	if err := r.ParseMultipartForm(20 << 20); err != nil { // 20 MB
		http.Error(w, "failed to parse form", http.StatusBadRequest)
		return
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		http.Error(w, "file field is required", http.StatusBadRequest)
		return
	}
	defer file.Close()

	// Local disk storage for dev
	uploadsDir := "uploads"
	if err := os.MkdirAll(uploadsDir, 0755); err != nil {
		http.Error(w, "failed to create uploads directory", http.StatusInternalServerError)
		return
	}

	filename := header.Filename
	storagePath := filepath.Join(uploadsDir, filename)

	dst, err := os.Create(storagePath)
	if err != nil {
		http.Error(w, "failed to save file", http.StatusInternalServerError)
		return
	}
	defer dst.Close()

	if _, err := dst.ReadFrom(file); err != nil {
		http.Error(w, "failed to write file", http.StatusInternalServerError)
		return
	}

	doc, err := s.db.CreateDocument(
		r.Context(),
		userID,
		header.Filename,
		header.Header.Get("Content-Type"),
		storagePath,
	)
	if err != nil {
		log.Printf("CreateDocument error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	writeJSON(w, http.StatusCreated, doc)
}

// List all documents for the authenticated user.
func (s *Server) handleListDocuments(w http.ResponseWriter, r *http.Request) {
	userID, ok := userIDFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	docs, err := s.db.ListDocumentsByUser(r.Context(), userID)
	if err != nil {
		log.Printf("ListDocumentsByUser error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	writeJSON(w, http.StatusOK, docs)
}

// ----- Single document routes: /documents/{id} and /documents/{id}/insight -----

func (s *Server) handleDocumentByID(w http.ResponseWriter, r *http.Request) {
	// Path is /documents/{id} or /documents/{id}/insight
	path := strings.TrimPrefix(r.URL.Path, "/documents/")
	if path == "" {
		w.WriteHeader(http.StatusNotFound)
		return
	}

	parts := strings.Split(path, "/")
	docID := parts[0]

	// /documents/{id}/insight
	if len(parts) == 2 && parts[1] == "insight" {
		if r.Method != http.MethodGet {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		s.handleGetDocumentInsight(w, r, docID)
		return
	}

	// /documents/{id} only (no further segments)
	if len(parts) > 1 {
		w.WriteHeader(http.StatusNotFound)
		return
	}

	switch r.Method {
	case http.MethodDelete:
		s.handleDeleteDocument(w, r, docID)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// Delete a document for the authenticated user.
func (s *Server) handleDeleteDocument(w http.ResponseWriter, r *http.Request, docID string) {
	userID, ok := userIDFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	deleted, err := s.db.DeleteDocumentByIDAndUser(r.Context(), docID, userID)
	if err != nil {
		log.Printf("DeleteDocumentByIDAndUser error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	if !deleted {
		// Either doc not found, or not owned by this user
		http.Error(w, "not found", http.StatusNotFound)
		return
	}

	// No body for delete success.
	w.WriteHeader(http.StatusNoContent)
}

// ----- Get insights for a document: GET /documents/{id}/insight -----

type insightResponse struct {
	DocumentID   string  `json:"document_id"`
	Status       string  `json:"status"`                  // "ready" | "processing" | "failed"
	InsightsHTML *string `json:"insights_html,omitempty"` // present when ready
	ErrorMessage *string `json:"error_message,omitempty"` // present when failed
}

func (s *Server) handleGetDocumentInsight(w http.ResponseWriter, r *http.Request, docID string) {
	userID, ok := userIDFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	ctx := r.Context()

	// 1) Ensure document belongs to this user
	doc, err := s.db.GetDocumentByIDAndUser(ctx, docID, userID)
	if err != nil {
		log.Printf("GetDocumentByIDAndUser error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if doc == nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}

	// 2) Try to fetch insight for this document
	ins, err := s.db.GetInsightByDocumentID(ctx, docID)
	if err != nil {
		log.Printf("GetInsightByDocumentID error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	// No insight row yet → still processing
	if ins == nil {
		resp := insightResponse{
			DocumentID: docID,
			Status:     "processing",
		}
		writeJSON(w, http.StatusOK, resp)
		return
	}

	// Map DB insight fields to API response
	resp := insightResponse{
		DocumentID:   docID,
		Status:       ins.Status, // e.g. "completed", "failed"
		InsightsHTML: nil,
		ErrorMessage: nil,
	}

	if ins.Status == "completed" {
		resp.InsightsHTML = &ins.HTMLInsights
	} else if ins.Status == "failed" && ins.ErrorMessage != nil {
		resp.ErrorMessage = ins.ErrorMessage
	}

	writeJSON(w, http.StatusOK, resp)
}

// ----- Get patient insights: GET /user/insights -----

type patientInsightResponse struct {
	Status       string  `json:"status"`                  // "completed" | "none"
	InsightsHTML *string `json:"insights_html,omitempty"` // present when completed
}

func (s *Server) handleGetPatientInsights(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}

	userID, ok := userIDFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	user, err := s.db.GetUserByID(r.Context(), userID)
	if err != nil {
		log.Printf("GetUserByID error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	resp := patientInsightResponse{
		Status: "none",
	}

	if user.PatientInsights != nil && *user.PatientInsights != "" {
		resp.Status = "completed"
		resp.InsightsHTML = user.PatientInsights
	}

	writeJSON(w, http.StatusOK, resp)
}
