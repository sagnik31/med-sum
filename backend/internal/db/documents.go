package db

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

// Document maps to the `documents` table. The extracted Markdown is left
// out: only the AI service reads it.
type Document struct {
	ID           string    `json:"id"`
	UserID       string    `json:"user_id"`
	OriginalName string    `json:"original_name"`
	ContentType  string    `json:"content_type"`
	StoragePath  string    `json:"-"` // server-side path; never sent to clients
	UploadedAt   time.Time `json:"uploaded_at"`
	UpdatedAt    time.Time `json:"updated_at"`
	// Status of the document's insight job ("not_started" when there is
	// none). Only set by ListDocumentsByUser.
	InsightStatus string `json:"insight_status,omitempty"`
}

// CreateDocument inserts a new row when a user uploads a file.
func (s *Store) CreateDocument(
	ctx context.Context,
	userID string,
	originalName string,
	contentType string,
	storagePath string,
) (*Document, error) {
	const q = `
INSERT INTO documents (user_id, original_name, content_type, storage_path)
VALUES ($1, $2, $3, $4)
RETURNING id, user_id, original_name, content_type, storage_path, uploaded_at, updated_at;
`
	row := s.pool.QueryRow(ctx, q, userID, originalName, contentType, storagePath)

	var d Document
	if err := row.Scan(
		&d.ID,
		&d.UserID,
		&d.OriginalName,
		&d.ContentType,
		&d.StoragePath,
		&d.UploadedAt,
		&d.UpdatedAt,
	); err != nil {
		return nil, err
	}
	return &d, nil
}

// ListDocumentsByUser lists all documents for a given user (latest first),
// with the status of each document's insight job.
func (s *Store) ListDocumentsByUser(ctx context.Context, userID string) ([]Document, error) {
	const q = `
SELECT d.id, d.user_id, d.original_name, d.content_type, d.storage_path,
       d.uploaded_at, d.updated_at, COALESCE(i.status, 'not_started')
FROM documents d
LEFT JOIN insights i ON i.document_id = d.id
WHERE d.user_id = $1
ORDER BY d.uploaded_at DESC;
`
	rows, err := s.pool.Query(ctx, q, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	docs := []Document{} // encodes as [] rather than null when empty
	for rows.Next() {
		var d Document
		if err := rows.Scan(
			&d.ID,
			&d.UserID,
			&d.OriginalName,
			&d.ContentType,
			&d.StoragePath,
			&d.UploadedAt,
			&d.UpdatedAt,
			&d.InsightStatus,
		); err != nil {
			return nil, err
		}
		docs = append(docs, d)
	}

	if err := rows.Err(); err != nil {
		return nil, err
	}
	return docs, nil
}

// DeleteDocumentByIDAndUser deletes a document if it belongs to the given user.
// It returns the deleted row's storage path ("" if no row matched), and
// whether another document still points at that file (possible for uploads
// saved before files got unique names).
func (s *Store) DeleteDocumentByIDAndUser(ctx context.Context, docID, userID string) (string, bool, error) {
	// The outer SELECT still sees the deleted row, hence d.id <> $1.
	const q = `
WITH deleted AS (
    DELETE FROM documents
    WHERE id = $1 AND user_id = $2
    RETURNING storage_path
)
SELECT storage_path,
       EXISTS (SELECT 1 FROM documents d WHERE d.storage_path = deleted.storage_path AND d.id <> $1)
FROM deleted;
`
	var storagePath string
	var shared bool
	err := s.pool.QueryRow(ctx, q, docID, userID).Scan(&storagePath, &shared)
	if err == pgx.ErrNoRows {
		return "", false, nil
	}
	if err != nil {
		return "", false, err
	}
	return storagePath, shared, nil
}
