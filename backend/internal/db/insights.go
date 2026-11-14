package db

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

// Insight represents a row in the insights table: where a document's
// processing stands.
type Insight struct {
	DocumentID   string
	HTMLInsights string
	Status       string
	ErrorMessage *string // set by the AI service when status is "failed"
	UpdatedAt    *time.Time
}

// GetInsightByDocumentID returns the insight for a given document, or
// (nil, nil) if not found. Callers load the document first (GetDocument),
// which is where the organisation is checked.
func (s *Store) GetInsightByDocumentID(ctx context.Context, docID string) (*Insight, error) {
	const q = `
SELECT document_id, COALESCE(html_insights, ''), status, error_message, updated_at
FROM insights
WHERE document_id = $1;
`
	var ins Insight
	err := s.pool.QueryRow(ctx, q, docID).Scan(
		&ins.DocumentID, &ins.HTMLInsights, &ins.Status, &ins.ErrorMessage, &ins.UpdatedAt,
	)
	if err == pgx.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &ins, nil
}
