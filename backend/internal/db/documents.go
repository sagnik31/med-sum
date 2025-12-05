package db

import (
	"context"
	"time"
)

// Document maps to the `documents` table.
type Document struct {
	ID                string    `json:"id"`
	UserID            string    `json:"user_id"`
	OriginalName      string    `json:"original_name"`
	ContentType       string    `json:"content_type"`
	StoragePath       string    `json:"storage_path"`
	ExtractedMarkdown string    `json:"extracted_markdown"`
	Status            string    `json:"status"`
	UploadedAt        time.Time `json:"uploaded_at"`
	UpdatedAt         time.Time `json:"updated_at"`
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
INSERT INTO documents (user_id, original_name, content_type, storage_path, status)
VALUES ($1, $2, $3, $4, 'uploaded')
RETURNING id, user_id, original_name, content_type, storage_path, status, uploaded_at, updated_at;
`
	row := s.pool.QueryRow(ctx, q, userID, originalName, contentType, storagePath)

	var d Document
	if err := row.Scan(
		&d.ID,
		&d.UserID,
		&d.OriginalName,
		&d.ContentType,
		&d.StoragePath,
		&d.Status,
		&d.UploadedAt,
		&d.UpdatedAt,
	); err != nil {
		return nil, err
	}
	return &d, nil
}

// ListDocumentsByUser lists all documents for a given user (latest first).
func (s *Store) ListDocumentsByUser(ctx context.Context, userID string) ([]Document, error) {
	const q = `
SELECT id, user_id, original_name, content_type, storage_path, COALESCE(extracted_markdown, ''), status, uploaded_at, updated_at
FROM documents
WHERE user_id = $1
ORDER BY uploaded_at DESC;
`
	rows, err := s.pool.Query(ctx, q, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var docs []Document
	for rows.Next() {
		var d Document
		if err := rows.Scan(
			&d.ID,
			&d.UserID,
			&d.OriginalName,
			&d.ContentType,
			&d.StoragePath,
			&d.ExtractedMarkdown,
			&d.Status,
			&d.UploadedAt,
			&d.UpdatedAt,
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
// Returns true if a row was deleted, false if no matching row.
func (s *Store) DeleteDocumentByIDAndUser(ctx context.Context, docID, userID string) (bool, error) {
	const q = `
DELETE FROM documents
WHERE id = $1 AND user_id = $2;
`
	cmdTag, err := s.pool.Exec(ctx, q, docID, userID)
	if err != nil {
		return false, err
	}
	return cmdTag.RowsAffected() > 0, nil
}
