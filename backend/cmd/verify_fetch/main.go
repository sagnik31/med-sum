package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

type Insight struct {
	DocumentID   string
	UserID       string
	HTMLInsights string
	Status       string
	ErrorMessage *string
	DetailsJSON  json.RawMessage
	GeneratedAt  *time.Time
	UpdatedAt    *time.Time
}

func main() {
	dbURL := "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"
	ctx := context.Background()

	pool, err := pgxpool.New(ctx, dbURL)
	if err != nil {
		log.Fatalf("Unable to connect to database: %v", err)
	}
	defer pool.Close()

	// 1. Insert a dummy insight with NULL details_json and generated_at (simulating Python service)
	docID := "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
	userID := "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a22"
	insightID := "c0eebc99-9c0b-4ef8-bb6d-6bb9bd380a33"

	// Insert User
	_, err = pool.Exec(ctx, "INSERT INTO users (id, phone_number) VALUES ($1, '1234567890') ON CONFLICT (id) DO NOTHING", userID)
	if err != nil {
		log.Printf("Failed to insert user: %v", err)
	}

	// Insert Document
	_, err = pool.Exec(ctx, "INSERT INTO documents (id, user_id, original_name, content_type, storage_path, status) VALUES ($1, $2, 'test.pdf', 'application/pdf', 'path/to/file', 'uploaded') ON CONFLICT (id) DO NOTHING", docID, userID)
	if err != nil {
		log.Printf("Failed to insert document: %v", err)
	}

	// Insert Insight with NULLs
	_, err = pool.Exec(ctx, `
		INSERT INTO insights (id, document_id, user_id, html_insights, status, created_at, updated_at)
		VALUES ($1, $2, $3, '<html></html>', 'completed', NOW(), NOW())
		ON CONFLICT (document_id) DO NOTHING
	`, insightID, docID, userID)
	if err != nil {
		log.Fatalf("Failed to insert insight: %v", err)
	}

	// 2. Try to fetch it using the same logic as GetInsightByDocumentID
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
	err = pool.QueryRow(ctx, q, docID).Scan(
		&ins.DocumentID,
		&ins.UserID,
		&ins.HTMLInsights,
		&ins.Status,
		&ins.DetailsJSON,
		&ins.GeneratedAt,
		&ins.UpdatedAt,
	)
	if err != nil {
		log.Fatalf("Scan failed: %v", err)
	}

	fmt.Printf("Successfully fetched insight! Status: %s\n", ins.Status)
	fmt.Printf("DetailsJSON: %v\n", ins.DetailsJSON)
	fmt.Printf("GeneratedAt: %v\n", ins.GeneratedAt)
}
