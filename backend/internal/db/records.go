package db

import (
	"context"
	"encoding/json"
	"time"

	"github.com/jackc/pgx/v5"
)

// DocumentRecord is a row of document_records: the checked, structured
// record the AI service built from one document, as stored JSON.
type DocumentRecord struct {
	DocumentID    string
	SchemaVersion int
	Record        json.RawMessage
	RunID         *string // the pipeline run that produced it; nil if that run was deleted
	UpdatedAt     time.Time
}

// GetDocumentRecord returns the record of a document, or (nil, nil) when
// the document has none yet. Callers check ownership first.
func (s *Store) GetDocumentRecord(ctx context.Context, docID string) (*DocumentRecord, error) {
	const q = `
SELECT document_id, schema_version, record, run_id::text, updated_at
FROM document_records
WHERE document_id = $1;
`
	var r DocumentRecord
	var raw []byte
	err := s.pool.QueryRow(ctx, q, docID).Scan(
		&r.DocumentID, &r.SchemaVersion, &raw, &r.RunID, &r.UpdatedAt,
	)
	if err == pgx.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	r.Record = json.RawMessage(raw)
	return &r, nil
}
