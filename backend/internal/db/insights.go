package db

import (
	"context"
	"encoding/json"
	"time"

	"github.com/jackc/pgx/v5"
)

// Insight represents a row in the insights table.
// Adjust fields if you already have a different definition elsewhere.
type Insight struct {
	DocumentID   string
	UserID       string
	HTMLInsights string
	Status       string
	// Optional error message; we fake it as NULL for now since we don't
	// have a specific column in the DB.
	ErrorMessage *string
	DetailsJSON  json.RawMessage
	GeneratedAt  *time.Time
	UpdatedAt    *time.Time
}

// GetDocumentByIDAndUser returns a single document owned by userID,
// or (nil, nil) if not found.
func (s *Store) GetDocumentByIDAndUser(ctx context.Context, docID, userID string) (*Document, error) {
	const q = `
		SELECT
		  id,
		  user_id,
		  original_name,
		  content_type,
		  storage_path,
		  status,
		  uploaded_at,
		  updated_at
		FROM documents
		WHERE id = $1
		  AND user_id = $2
		LIMIT 1;
	`

	var d Document
	err := s.pool.QueryRow(ctx, q, docID, userID).Scan(
		&d.ID,
		&d.UserID,
		&d.OriginalName,
		&d.ContentType,
		&d.StoragePath,
		&d.Status,
		&d.UploadedAt,
		&d.UpdatedAt,
	)
	if err == pgx.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &d, nil
}

// GetInsightByDocumentID returns the insight for a given document,
// or (nil, nil) if not found.
func (s *Store) GetInsightByDocumentID(ctx context.Context, docID string) (*Insight, error) {
	const q = `
		SELECT
		  document_id,
		  user_id,
		  html_insights,
		  status,
		  details_json,
		  generated_at,
		  updated_at
		FROM insights
		WHERE document_id = $1
		LIMIT 1;
	`

	var ins Insight
	err := s.pool.QueryRow(ctx, q, docID).Scan(
		&ins.DocumentID,
		&ins.UserID,
		&ins.HTMLInsights,
		&ins.Status,
		&ins.DetailsJSON,
		&ins.GeneratedAt,
		&ins.UpdatedAt,
	)
	if err == pgx.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}

	// We don't have a dedicated error_message column yet, so leave it nil.
	ins.ErrorMessage = nil

	return &ins, nil
}