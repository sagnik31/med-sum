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
	// SHA256 of the uploaded file; kept server-side.
	SHA256 string `json:"-"`
	// DuplicateOf is the first document of the same patient with the same
	// file, when this one is a copy of it: a flag, not a refusal. Set by
	// CreateDocument and ListDocumentsByUser. See
	// docs/adr/0036-data-lifecycle.md.
	DuplicateOf *string `json:"duplicate_of,omitempty"`
}

// duplicateOf is the SQL for a document row d's DuplicateOf: the earliest
// other document of the same user with the same file hash.
const duplicateOf = `(
    SELECT o.id FROM documents o
    WHERE o.user_id = d.user_id AND o.sha256 = d.sha256
      AND (o.uploaded_at, o.id) < (d.uploaded_at, d.id)
    ORDER BY o.uploaded_at, o.id
    LIMIT 1)`

// CreateDocument inserts a new row when a user uploads a file, with the
// file's SHA-256 (hex), and says which earlier document it duplicates.
func (s *Store) CreateDocument(
	ctx context.Context,
	userID string,
	originalName string,
	contentType string,
	storagePath string,
	sha256 string,
) (*Document, error) {
	// RETURNING sees the table as it was before the insert, so the new
	// row can't name itself.
	const q = `
INSERT INTO documents AS d (user_id, original_name, content_type, storage_path, sha256)
VALUES ($1, $2, $3, $4, $5)
RETURNING id, user_id, original_name, content_type, storage_path, uploaded_at, updated_at,
    COALESCE(sha256, ''), (SELECT o.id FROM documents o WHERE o.user_id = $1 AND o.sha256 = $5
             ORDER BY o.uploaded_at, o.id LIMIT 1);
`
	row := s.pool.QueryRow(ctx, q, userID, originalName, contentType, storagePath, sha256)

	var d Document
	if err := row.Scan(
		&d.ID,
		&d.UserID,
		&d.OriginalName,
		&d.ContentType,
		&d.StoragePath,
		&d.UploadedAt,
		&d.UpdatedAt,
		&d.SHA256,
		&d.DuplicateOf,
	); err != nil {
		return nil, err
	}
	return &d, nil
}

// ListDocumentsByUser lists all documents for a given user (latest first),
// with the status of each document's insight job.
func (s *Store) ListDocumentsByUser(ctx context.Context, userID string) ([]Document, error) {
	q := `
SELECT d.id, d.user_id, d.original_name, d.content_type, d.storage_path,
       d.uploaded_at, d.updated_at, COALESCE(i.status, 'not_started'),
       COALESCE(d.sha256, ''), ` + duplicateOf + `
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
			&d.SHA256,
			&d.DuplicateOf,
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
