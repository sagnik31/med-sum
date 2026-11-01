package httpapi

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"backend/internal/db"
)

type Server struct {
	db            *db.Store
	jwtSecret     []byte
	uploadsDir    string // absolute, so the AI service can open stored paths
	insightsURL   string // AI service base URL
	internalToken string // shared secret the AI service requires
	httpClient    *http.Client
	mux           *http.ServeMux
}

func NewServer(store *db.Store, jwtSecret, uploadsDir, insightsURL, internalToken string) *Server {
	s := &Server{
		db:            store,
		jwtSecret:     []byte(jwtSecret),
		uploadsDir:    uploadsDir,
		insightsURL:   strings.TrimRight(insightsURL, "/"),
		internalToken: internalToken,
		httpClient:    &http.Client{Timeout: 10 * time.Second},
		mux:           http.NewServeMux(),
	}

	// Public
	s.mux.HandleFunc("/healthz", s.handleHealth)
	s.mux.HandleFunc("/login", s.handleLogin)

	// Authenticated:
	// /documents (POST = upload, GET = list)
	s.mux.HandleFunc("/documents", s.withAuth(s.handleDocuments))
	// /documents/{id} and /documents/{id}/insight (GET = fetch, POST = generate)
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
	_, err = dst.ReadFrom(file)
	if closeErr := dst.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		_ = os.Remove(storagePath)
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
		_ = os.Remove(storagePath)
		log.Printf("CreateDocument error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	writeJSON(w, http.StatusCreated, doc)
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
		switch r.Method {
		case http.MethodGet:
			s.handleGetDocumentInsight(w, r, docID)
		case http.MethodPost:
			s.handleGenerateDocumentInsight(w, r, docID)
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
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

	storagePath, shared, err := s.db.DeleteDocumentByIDAndUser(r.Context(), docID, userID)
	if err != nil {
		log.Printf("DeleteDocumentByIDAndUser error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	if storagePath == "" {
		// Either doc not found, or not owned by this user
		http.Error(w, "not found", http.StatusNotFound)
		return
	}

	// Remove the patient's file too, unless an older document shares it.
	if !shared {
		if err := os.Remove(storagePath); err != nil && !os.IsNotExist(err) {
			log.Printf("failed to remove %s: %v", storagePath, err)
		}
	}

	// No body for delete success.
	w.WriteHeader(http.StatusNoContent)
}

// ----- Get insights for a document: GET /documents/{id}/insight -----

type insightResponse struct {
	DocumentID   string  `json:"document_id"`
	Status       string  `json:"status"`                  // "completed" | "processing" | "failed"
	InsightsHTML *string `json:"insights_html,omitempty"` // present when completed
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

// ----- Start insight generation: POST /documents/{id}/insight -----

// The AI service trusts whatever document ID it is given, so the browser
// never calls it directly: requests come through here, where ownership is
// checked, and carry the shared internal token.
func (s *Server) handleGenerateDocumentInsight(w http.ResponseWriter, r *http.Request, docID string) {
	userID, ok := userIDFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	doc, err := s.db.GetDocumentByIDAndUser(r.Context(), docID, userID)
	if err != nil {
		log.Printf("GetDocumentByIDAndUser error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if doc == nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}

	body, _ := json.Marshal(map[string]string{"document_id": docID})
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost,
		s.insightsURL+"/internal/generate-insights", bytes.NewReader(body))
	if err != nil {
		log.Printf("build insights request: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Internal-Token", s.internalToken)

	resp, err := s.httpClient.Do(req)
	if err != nil {
		log.Printf("insights service error: %v", err)
		http.Error(w, "insights service unavailable", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		log.Printf("insights service returned %s", resp.Status)
		http.Error(w, "insights service unavailable", http.StatusBadGateway)
		return
	}

	writeJSON(w, http.StatusAccepted, map[string]string{"status": "accepted", "document_id": docID})
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
