package httpapi

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"
)

// ----- The structured record: GET /documents/{id}/record -----

// recordResponse is a document's stored record, as the AI service wrote
// it (every item with its printed values, derived values, citation and
// provenance, and the page transcriptions the citations point into), plus
// a flat list of those citations so a client need not walk the record.
type recordResponse struct {
	DocumentID    string           `json:"document_id"`
	Status        string           `json:"status"` // the document's processing status, as on /insight
	SchemaVersion int              `json:"schema_version"`
	RunID         *string          `json:"run_id"`
	UpdatedAt     time.Time        `json:"updated_at"`
	Record        json.RawMessage  `json:"record"`
	Citations     []citationResult `json:"citations"`
}

// citationResult is where one printed value is on the page transcriptions,
// with the review state of the item it belongs to.
type citationResult struct {
	Group     string   `json:"group"`           // observations, medications, ..., patient, document
	ItemID    string   `json:"item_id"`         // the item's id; "patient" or "document" for header values
	Field     *string  `json:"field,omitempty"` // which header value, e.g. name or clinicians[0]
	Page      int      `json:"page"`
	FirstLine int      `json:"first_line"`
	LastLine  int      `json:"last_line"`
	Quote     string   `json:"quote"`
	Status    *string  `json:"status"`  // extracted, validated, low_confidence, ...; null without provenance
	Reasons   []string `json:"reasons"` // why it is low confidence, in plain words
}

func (s *Server) handleGetDocumentRecord(w http.ResponseWriter, r *http.Request, docID string) {
	userID, ok := userIDFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	ctx := r.Context()

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
	status := "not_started"
	if ins != nil {
		status = ins.Status
	}

	citations, err := recordCitations(rec.Record)
	if err != nil {
		// The AI service validates every record before storing it, so this
		// is a bug; the record itself holds patient data and isn't logged.
		log.Printf("record of document %s is unreadable: %v", docID, err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	writeJSON(w, http.StatusOK, recordResponse{
		DocumentID:    docID,
		Status:        status,
		SchemaVersion: rec.SchemaVersion,
		RunID:         rec.RunID,
		UpdatedAt:     rec.UpdatedAt,
		Record:        rec.Record,
		Citations:     citations,
	})
}

// The parts of a stored record the citation list reads. Everything else
// in the record is passed through untouched.
type recCitation struct {
	Page      int    `json:"page"`
	FirstLine int    `json:"first_line"`
	LastLine  int    `json:"last_line"`
	Quote     string `json:"quote"`
}

type recProvenance struct {
	Status  *string  `json:"status"`
	Reasons []string `json:"low_confidence_reasons"`
}

type recText struct {
	Citation recCitation `json:"citation"`
}

type recItem struct {
	ID      string `json:"id"`
	Printed struct {
		Citation *recCitation `json:"citation"`
	} `json:"printed"`
	Provenance *recProvenance `json:"provenance"`
}

type recShape struct {
	Document struct {
		Printed struct {
			Facility         *recText  `json:"facility"`
			Clinicians       []recText `json:"clinicians"`
			ReportDate       *recText  `json:"report_date"`
			CollectionDate   *recText  `json:"collection_date"`
			PrescriptionDate *recText  `json:"prescription_date"`
		} `json:"printed"`
		Provenance *recProvenance `json:"provenance"`
	} `json:"document"`
	Patient *struct {
		Printed struct {
			Name        *recText `json:"name"`
			Age         *recText `json:"age"`
			Sex         *recText `json:"sex"`
			DateOfBirth *recText `json:"date_of_birth"`
			PatientID   *recText `json:"patient_id"`
		} `json:"printed"`
		Provenance *recProvenance `json:"provenance"`
	} `json:"patient"`
	Observations    []recItem `json:"observations"`
	Medications     []recItem `json:"medications"`
	Conditions      []recItem `json:"conditions"`
	Remarks         []recItem `json:"remarks"`
	Vitals          []recItem `json:"vitals"`
	Allergies       []recItem `json:"allergies"`
	Procedures      []recItem `json:"procedures"`
	Encounters      []recItem `json:"encounters"`
	ImagingFindings []recItem `json:"imaging_findings"`
}

// recordCitations lists every citation in a record, in the record's order:
// the header values (patient, then document), then each clinical item.
func recordCitations(raw json.RawMessage) ([]citationResult, error) {
	var rec recShape
	if err := json.Unmarshal(raw, &rec); err != nil {
		return nil, err
	}
	out := []citationResult{}

	add := func(group, itemID string, field *string, c recCitation, p *recProvenance) {
		res := citationResult{
			Group: group, ItemID: itemID, Field: field,
			Page: c.Page, FirstLine: c.FirstLine, LastLine: c.LastLine, Quote: c.Quote,
			Reasons: []string{},
		}
		if p != nil {
			res.Status = p.Status
			if p.Reasons != nil {
				res.Reasons = p.Reasons
			}
		}
		out = append(out, res)
	}
	header := func(group string, field string, t *recText, p *recProvenance) {
		if t != nil {
			f := field
			add(group, group, &f, t.Citation, p)
		}
	}

	if pt := rec.Patient; pt != nil {
		header("patient", "name", pt.Printed.Name, pt.Provenance)
		header("patient", "age", pt.Printed.Age, pt.Provenance)
		header("patient", "sex", pt.Printed.Sex, pt.Provenance)
		header("patient", "date_of_birth", pt.Printed.DateOfBirth, pt.Provenance)
		header("patient", "patient_id", pt.Printed.PatientID, pt.Provenance)
	}
	d := rec.Document
	header("document", "facility", d.Printed.Facility, d.Provenance)
	for i := range d.Printed.Clinicians {
		header("document", fmt.Sprintf("clinicians[%d]", i), &d.Printed.Clinicians[i], d.Provenance)
	}
	header("document", "report_date", d.Printed.ReportDate, d.Provenance)
	header("document", "collection_date", d.Printed.CollectionDate, d.Provenance)
	header("document", "prescription_date", d.Printed.PrescriptionDate, d.Provenance)

	groups := []struct {
		name  string
		items []recItem
	}{
		{"observations", rec.Observations},
		{"medications", rec.Medications},
		{"conditions", rec.Conditions},
		{"remarks", rec.Remarks},
		{"vitals", rec.Vitals},
		{"allergies", rec.Allergies},
		{"procedures", rec.Procedures},
		{"encounters", rec.Encounters},
		{"imaging_findings", rec.ImagingFindings},
	}
	for _, g := range groups {
		for _, it := range g.items {
			if it.Printed.Citation == nil {
				return nil, fmt.Errorf("%s item %q has no citation", g.name, it.ID)
			}
			add(g.name, it.ID, nil, *it.Printed.Citation, it.Provenance)
		}
	}
	return out, nil
}
