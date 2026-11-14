package httpapi

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"

	"backend/internal/authz"
	"backend/internal/db"
)

// Patients are records, not accounts. Every clinician of an organisation
// sees every patient of it; a patient of another organisation answers
// 404, as if it didn't exist. The store filters on the caller's
// organisation in SQL. See docs/adr/0010-staff-access-and-security.md.
//
//	GET  /patients?q=          list or search (name, or the start of the MRN)
//	POST /patients             create
//	GET  /patients/{id}        details
//	PATCH /patients/{id}       change details
//	GET  /patients/{id}/documents   the patient's documents
//	POST /patients/{id}/documents   upload one (multipart, field "file")
//	GET  /patients/{id}/summary     the patient summary and its status
//	POST /patients/{id}/summary     (re)build it in the background

func (s *Server) handlePatientByID(w http.ResponseWriter, r *http.Request) {
	parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/patients/"), "/")
	if parts[0] == "" || len(parts) > 2 {
		w.WriteHeader(http.StatusNotFound)
		return
	}
	if !validUUID(parts[0]) {
		http.Error(w, "invalid patient id", http.StatusBadRequest)
		return
	}
	id := parts[0]
	var routes map[string]methodRoute
	switch {
	case len(parts) == 1:
		routes = map[string]methodRoute{
			http.MethodGet:   {authz.PatientRead, func(w http.ResponseWriter, r *http.Request) { s.handleGetPatient(w, r, id) }},
			http.MethodPatch: {authz.PatientWrite, func(w http.ResponseWriter, r *http.Request) { s.handleUpdatePatient(w, r, id) }},
		}
	case parts[1] == "documents":
		routes = map[string]methodRoute{
			http.MethodGet:  {authz.DocumentRead, func(w http.ResponseWriter, r *http.Request) { s.handleListPatientDocuments(w, r, id) }},
			http.MethodPost: {authz.DocumentUpload, func(w http.ResponseWriter, r *http.Request) { s.handleUploadDocument(w, r, id) }},
		}
	case parts[1] == "summary":
		routes = map[string]methodRoute{
			http.MethodGet:  {authz.SummaryRead, func(w http.ResponseWriter, r *http.Request) { s.handleGetPatientSummary(w, r, id) }},
			http.MethodPost: {authz.SummaryGenerate, func(w http.ResponseWriter, r *http.Request) { s.handleGeneratePatientSummary(w, r, id) }},
		}
	default:
		w.WriteHeader(http.StatusNotFound)
		return
	}
	byMethod(routes)(w, r)
}

// loadPatient returns the caller's organisation's patient, or answers 404
// (or 500) and returns nil.
func (s *Server) loadPatient(w http.ResponseWriter, r *http.Request, id string) *db.Patient {
	p, _ := principalFromContext(r.Context())
	patient, err := s.db.GetPatient(r.Context(), p.OrgID, id)
	if err != nil {
		log.Printf("GetPatient error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return nil
	}
	if patient == nil {
		http.Error(w, "not found", http.StatusNotFound)
		return nil
	}
	return patient
}

func (s *Server) handleListPatients(w http.ResponseWriter, r *http.Request) {
	p, _ := principalFromContext(r.Context())
	q := r.URL.Query().Get("q")
	if utf8.RuneCountInString(q) > 100 {
		http.Error(w, "search text too long", http.StatusBadRequest)
		return
	}
	patients, err := s.db.ListPatients(r.Context(), p.OrgID, q)
	if err != nil {
		log.Printf("ListPatients error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusOK, patients)
}

func (s *Server) handleGetPatient(w http.ResponseWriter, r *http.Request, id string) {
	if patient := s.loadPatient(w, r, id); patient != nil {
		writeJSON(w, http.StatusOK, patient)
	}
}

// ----- Patient details: validation shared by create and update -----

// optional is a JSON field that may be absent, null or a value, so a
// PATCH can tell "leave as is" from "clear".
type optional[T any] struct {
	Set   bool
	Value *T
}

func (o *optional[T]) UnmarshalJSON(b []byte) error {
	o.Set = true
	if bytes.Equal(bytes.TrimSpace(b), []byte("null")) {
		o.Value = nil
		return nil
	}
	var v T
	if err := json.Unmarshal(b, &v); err != nil {
		return err
	}
	o.Value = &v
	return nil
}

type patientInput struct {
	MRN         optional[string] `json:"mrn"`
	FullName    optional[string] `json:"full_name"`
	DateOfBirth optional[string] `json:"date_of_birth"`
	AgeYears    optional[int]    `json:"age_years"`
	Sex         optional[string] `json:"sex"`
}

func decodePatientInput(w http.ResponseWriter, r *http.Request) (patientInput, error) {
	var in patientInput
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<14))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&in); err != nil {
		return in, errors.New("invalid JSON body")
	}
	return in, nil
}

// apply puts the fields the input sets onto f.
func (in patientInput) apply(f *db.PatientFields) {
	if in.MRN.Set {
		f.MRN = ""
		if in.MRN.Value != nil {
			f.MRN = strings.TrimSpace(*in.MRN.Value)
		}
	}
	if in.FullName.Set {
		f.FullName = ""
		if in.FullName.Value != nil {
			f.FullName = strings.Join(strings.Fields(*in.FullName.Value), " ")
		}
	}
	if in.DateOfBirth.Set {
		f.DateOfBirth = in.DateOfBirth.Value
	}
	if in.AgeYears.Set {
		f.AgeYears = in.AgeYears.Value
	}
	if in.Sex.Set {
		f.Sex = in.Sex.Value
	}
}

// checkPatientFields says what is wrong with a patient's details, or "".
// The MRN is required: the patient-match check compares the ID printed
// on a document with it.
func checkPatientFields(f db.PatientFields, today time.Time) string {
	switch {
	case f.MRN == "":
		return "mrn is required"
	case utf8.RuneCountInString(f.MRN) > 64:
		return "mrn is too long (max 64 characters)"
	case f.FullName == "":
		return "full_name is required"
	case utf8.RuneCountInString(f.FullName) > 200:
		return "full_name is too long (max 200 characters)"
	case f.DateOfBirth != nil && f.AgeYears != nil:
		return "give a date_of_birth or an age_years, not both"
	case f.AgeYears != nil && (*f.AgeYears < 0 || *f.AgeYears > 130):
		return "age_years must be between 0 and 130"
	case f.Sex != nil && *f.Sex != "F" && *f.Sex != "M" && *f.Sex != "O":
		return `sex must be "F", "M" or "O"`
	}
	if f.DateOfBirth != nil {
		dob, err := time.Parse("2006-01-02", *f.DateOfBirth)
		if err != nil {
			return "date_of_birth must be a date, YYYY-MM-DD"
		}
		if dob.After(today) {
			return "date_of_birth is in the future"
		}
	}
	return ""
}

func (s *Server) handleCreatePatient(w http.ResponseWriter, r *http.Request) {
	p, _ := principalFromContext(r.Context())
	in, err := decodePatientInput(w, r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	var f db.PatientFields
	in.apply(&f)
	if msg := checkPatientFields(f, s.now()); msg != "" {
		http.Error(w, msg, http.StatusBadRequest)
		return
	}
	patient, err := s.db.CreatePatient(r.Context(), p.OrgID, p.UserID, f)
	if errors.Is(err, db.ErrMRNTaken) {
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}
	if err != nil {
		log.Printf("CreatePatient error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusCreated, patient)
}

func (s *Server) handleUpdatePatient(w http.ResponseWriter, r *http.Request, id string) {
	p, _ := principalFromContext(r.Context())
	in, err := decodePatientInput(w, r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	cur := s.loadPatient(w, r, id)
	if cur == nil {
		return
	}
	f := db.PatientFields{MRN: cur.MRN, FullName: cur.FullName, DateOfBirth: cur.DateOfBirth,
		AgeYears: cur.AgeYears, Sex: cur.Sex}
	in.apply(&f)
	if msg := checkPatientFields(f, s.now()); msg != "" {
		http.Error(w, msg, http.StatusBadRequest)
		return
	}
	patient, err := s.db.UpdatePatient(r.Context(), p.OrgID, id, f)
	if errors.Is(err, db.ErrMRNTaken) {
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}
	if err != nil {
		log.Printf("UpdatePatient error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if patient == nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	writeJSON(w, http.StatusOK, patient)
}

// ----- A patient's documents -----

func (s *Server) handleListPatientDocuments(w http.ResponseWriter, r *http.Request, id string) {
	p, _ := principalFromContext(r.Context())
	if s.loadPatient(w, r, id) == nil {
		return
	}
	docs, err := s.db.ListPatientDocuments(r.Context(), p.OrgID, id)
	if err != nil {
		log.Printf("ListPatientDocuments error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusOK, docs)
}

// ----- The patient summary -----

type patientSummaryResponse struct {
	PatientID    string     `json:"patient_id"`
	Status       string     `json:"status"`                  // "none" | "processing" | "completed" | "failed"
	InsightsHTML *string    `json:"insights_html,omitempty"` // the latest summary, kept while a new one is built
	ErrorMessage *string    `json:"error_message,omitempty"` // present when failed
	UpdatedAt    *time.Time `json:"updated_at,omitempty"`
}

func (s *Server) handleGetPatientSummary(w http.ResponseWriter, r *http.Request, id string) {
	if s.loadPatient(w, r, id) == nil {
		return
	}
	summary, err := s.db.GetPatientSummary(r.Context(), id)
	if err != nil {
		log.Printf("GetPatientSummary error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	resp := patientSummaryResponse{PatientID: id, Status: "none", UpdatedAt: summary.UpdatedAt}
	if summary.HTML != nil && *summary.HTML != "" {
		resp.InsightsHTML = summary.HTML
	}
	if summary.Status != nil {
		resp.Status = *summary.Status
	}
	if resp.Status == "failed" {
		resp.ErrorMessage = summary.Error
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) handleGeneratePatientSummary(w http.ResponseWriter, r *http.Request, id string) {
	if s.loadPatient(w, r, id) == nil {
		return
	}
	if err := s.requestPatientSummary(r, id); err != nil {
		log.Printf("insights service error: %v", err)
		http.Error(w, "insights service unavailable", http.StatusBadGateway)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]string{"status": "accepted", "patient_id": id})
}

// requestPatientSummary asks the AI service to (re)build a patient's
// summary. The AI service acts on any patient ID, so callers check the
// organisation first.
func (s *Server) requestPatientSummary(r *http.Request, patientID string) error {
	err := s.callInsightsService(r.Context(), "/internal/generate-patient-summary",
		map[string]string{"patient_id": patientID})
	if err != nil {
		return fmt.Errorf("patient summary: %w", err)
	}
	return nil
}
