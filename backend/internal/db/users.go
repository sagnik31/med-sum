package db

import (
	"context"
	"time"
)

// User maps to the `users` table.
type User struct {
	ID              string     `json:"id"`
	PhoneNumber     string     `json:"phone_number"`
	FullName        *string    `json:"full_name,omitempty"`
	PatientInsights *string    `json:"patient_insights,omitempty"`
	CreatedAt       time.Time  `json:"created_at"`
	LastLoginAt     *time.Time `json:"last_login_at,omitempty"`
}

// UpsertUserByPhone finds a user by phone_number or creates one.
// It also updates last_login_at to NOW() on each call, and full_name when
// one is given.
func (s *Store) UpsertUserByPhone(ctx context.Context, phone string, fullName *string) (*User, error) {
	const q = `
INSERT INTO users (phone_number, full_name, last_login_at)
VALUES ($1, $2, NOW())
ON CONFLICT (phone_number)
DO UPDATE SET
    last_login_at = EXCLUDED.last_login_at,
    full_name = COALESCE(EXCLUDED.full_name, users.full_name)
RETURNING id, phone_number, full_name, patient_insights, created_at, last_login_at;
`
	row := s.pool.QueryRow(ctx, q, phone, fullName)

	var u User
	if err := row.Scan(
		&u.ID,
		&u.PhoneNumber,
		&u.FullName,
		&u.PatientInsights,
		&u.CreatedAt,
		&u.LastLoginAt,
	); err != nil {
		return nil, err
	}
	return &u, nil
}

// PatientSummary is the cumulative summary stored on a user's row.
type PatientSummary struct {
	HTML      *string
	Status    *string // "processing", "completed" or "failed"; nil if never requested
	Error     *string // set when Status is "failed"
	UpdatedAt *time.Time
}

// GetPatientSummary returns the user's patient summary and its job status.
func (s *Store) GetPatientSummary(ctx context.Context, userID string) (*PatientSummary, error) {
	const q = `
SELECT patient_insights, patient_insights_status, patient_insights_error, patient_insights_updated_at
FROM users
WHERE id = $1;
`
	var p PatientSummary
	if err := s.pool.QueryRow(ctx, q, userID).Scan(&p.HTML, &p.Status, &p.Error, &p.UpdatedAt); err != nil {
		return nil, err
	}
	return &p, nil
}
