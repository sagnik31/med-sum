package db

import (
	"context"
	"time"
)

// User maps to the `users` table.
type User struct {
	ID          string     `json:"id"`
	PhoneNumber string     `json:"phone_number"`
	FullName    *string    `json:"full_name,omitempty"`
	CreatedAt   time.Time  `json:"created_at"`
	LastLoginAt *time.Time `json:"last_login_at,omitempty"`
}

// UpsertUserByPhone finds a user by phone_number or creates one.
// It also updates last_login_at to NOW() on each call.
func (s *Store) UpsertUserByPhone(ctx context.Context, phone string, fullName *string) (*User, error) {
	const q = `
INSERT INTO users (phone_number, full_name, last_login_at)
VALUES ($1, $2, NOW())
ON CONFLICT (phone_number)
DO UPDATE SET
    last_login_at = EXCLUDED.last_login_at
RETURNING id, phone_number, full_name, created_at, last_login_at;
`
	row := s.pool.QueryRow(ctx, q, phone, fullName)

	var u User
	if err := row.Scan(
		&u.ID,
		&u.PhoneNumber,
		&u.FullName,
		&u.CreatedAt,
		&u.LastLoginAt,
	); err != nil {
		return nil, err
	}
	return &u, nil
}

// GetUserByID fetches a user by id.
func (s *Store) GetUserByID(ctx context.Context, id string) (*User, error) {
	const q = `
SELECT id, phone_number, full_name, created_at, last_login_at
FROM users
WHERE id = $1;
`
	row := s.pool.QueryRow(ctx, q, id)

	var u User
	if err := row.Scan(
		&u.ID,
		&u.PhoneNumber,
		&u.FullName,
		&u.CreatedAt,
		&u.LastLoginAt,
	); err != nil {
		return nil, err
	}
	return &u, nil
}