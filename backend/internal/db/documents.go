package db

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
)

// Document maps to the `documents` table. The extracted Markdown is left
// out: only the AI service reads it. A document belongs to a patient, and
// is found only through the caller's organisation (the patient's); a
// deleted document (soft delete) is never returned.
type Document struct {
	ID             string    `json:"id"`
	PatientID      string    `json:"patient_id"`
	UploadedBy     *string   `json:"uploaded_by"`      // the uploader's account; null for rows made without an upload
	UploadedByName *string   `json:"uploaded_by_name"` // their name, for "uploaded by"
	OriginalName   string    `json:"original_name"`
	ContentType    string    `json:"content_type"`
	StoragePath    string    `json:"-"` // server-side path; never sent to clients
	UploadedAt     time.Time `json:"uploaded_at"`
	UpdatedAt      time.Time `json:"updated_at"`
	// Status of the document's insight job ("not_started" when there is
	// none). Set by ListPatientDocuments.
	InsightStatus string `json:"insight_status,omitempty"`
	// SHA256 of the uploaded file; kept server-side.
	SHA256 string `json:"-"`
	// DuplicateOf is the first live document of the same patient with the
	// same file, when this one is a copy of it: a flag, not a refusal. See
	// docs/adr/0008-patient-data-protection.md.
	DuplicateOf *string `json:"duplicate_of,omitempty"`
}

// duplicateOf is the SQL for a document row d's DuplicateOf: the earliest
// other live document of the same patient with the same file hash.
const duplicateOf = `(
    SELECT o.id FROM documents o
    WHERE o.patient_id = d.patient_id AND o.sha256 = d.sha256 AND o.deleted_at IS NULL
      AND (o.uploaded_at, o.id) < (d.uploaded_at, d.id)
    ORDER BY o.uploaded_at, o.id
    LIMIT 1)`

const documentColumns = `d.id, d.patient_id, d.uploaded_by, u.full_name, d.original_name, d.content_type,
       d.storage_path, d.uploaded_at, d.updated_at, COALESCE(d.sha256, ''), ` + duplicateOf

// documentFrom joins what every document query needs: the patient (for
// the organisation) and the uploader (for the name).
const documentFrom = `
FROM documents d
JOIN patients p ON p.id = d.patient_id
LEFT JOIN staff_users u ON u.id = d.uploaded_by`

func scanDocument(row pgx.Row, extra ...any) (*Document, error) {
	var d Document
	dest := append([]any{&d.ID, &d.PatientID, &d.UploadedBy, &d.UploadedByName, &d.OriginalName,
		&d.ContentType, &d.StoragePath, &d.UploadedAt, &d.UpdatedAt, &d.SHA256, &d.DuplicateOf}, extra...)
	err := row.Scan(dest...)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &d, nil
}

// NewDocument is an uploaded file to record for a patient.
type NewDocument struct {
	PatientID    string
	UploadedBy   string
	OriginalName string
	ContentType  string
	StoragePath  string
	SHA256       string // hex
}

// CreateDocument records an upload for a patient of the organisation, and
// says which earlier document it duplicates. It returns nil when the
// organisation has no such patient.
func (s *Store) CreateDocument(ctx context.Context, orgID string, nd NewDocument) (*Document, error) {
	var id string
	err := s.pool.QueryRow(ctx, `
INSERT INTO documents (patient_id, uploaded_by, original_name, content_type, storage_path, sha256)
SELECT p.id, $3, $4, $5, $6, $7 FROM patients p WHERE p.id = $1 AND p.org_id = $2
RETURNING id`,
		nd.PatientID, orgID, nd.UploadedBy, nd.OriginalName, nd.ContentType, nd.StoragePath, nd.SHA256,
	).Scan(&id)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return s.GetDocument(ctx, orgID, id)
}

// GetDocument returns a live document of the organisation, or nil.
func (s *Store) GetDocument(ctx context.Context, orgID, docID string) (*Document, error) {
	return scanDocument(s.pool.QueryRow(ctx, `SELECT `+documentColumns+documentFrom+`
WHERE d.id = $1 AND p.org_id = $2 AND d.deleted_at IS NULL`, docID, orgID))
}

// ListPatientDocuments lists a patient's live documents, latest first,
// with the status of each one's insight job. Callers load the patient
// first; the organisation is checked here as well.
func (s *Store) ListPatientDocuments(ctx context.Context, orgID, patientID string) ([]Document, error) {
	rows, err := s.pool.Query(ctx, `SELECT `+documentColumns+`, COALESCE(i.status, 'not_started')`+documentFrom+`
LEFT JOIN insights i ON i.document_id = d.id
WHERE d.patient_id = $1 AND p.org_id = $2 AND d.deleted_at IS NULL
ORDER BY d.uploaded_at DESC, d.id`, patientID, orgID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	docs := []Document{} // encodes as [] rather than null when empty
	for rows.Next() {
		var status string
		d, err := scanDocument(rows, &status)
		if err != nil {
			return nil, err
		}
		d.InsightStatus = status
		docs = append(docs, *d)
	}
	return docs, rows.Err()
}

// SoftDeleteDocument marks a live document of the organisation deleted,
// by the given account. It reports whether a row was marked. The row and
// its file stay; every query that shows or uses documents leaves it out.
func (s *Store) SoftDeleteDocument(ctx context.Context, orgID, docID, deletedBy string, now time.Time) (bool, error) {
	tag, err := s.pool.Exec(ctx, `
UPDATE documents d SET deleted_at = $4, deleted_by = $3, updated_at = $4
FROM patients p
WHERE d.id = $1 AND p.id = d.patient_id AND p.org_id = $2 AND d.deleted_at IS NULL`,
		docID, orgID, deletedBy, now)
	if err != nil {
		return false, err
	}
	return tag.RowsAffected() == 1, nil
}
