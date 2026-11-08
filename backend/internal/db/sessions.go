package db

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
)

// Session maps to the `sessions` table. TokenHash is the SHA-256 of the
// cookie value; the value itself is never stored.
type Session struct {
	TokenHash  []byte
	ID         string // set by the database; names the session to its owner
	UserID     string
	CreatedAt  time.Time
	LastSeenAt time.Time
	ExpiresAt  time.Time
	IP         string
	UserAgent  string
}

// SessionWithUser is a session and the account it belongs to.
type SessionWithUser struct {
	Session
	User User
}

// CreateSession inserts a session.
func (s *Store) CreateSession(ctx context.Context, sess Session) error {
	_, err := s.pool.Exec(ctx, `
INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, ip, user_agent)
VALUES ($1, $2, $3, $4, $5, $6, $7)`,
		sess.TokenHash, sess.UserID, sess.CreatedAt, sess.LastSeenAt, sess.ExpiresAt, sess.IP, sess.UserAgent)
	return err
}

// GetSession returns the session with that token hash and its account, or
// nil. It does not check expiry; the caller does, against its own clock.
func (s *Store) GetSession(ctx context.Context, tokenHash []byte) (*SessionWithUser, error) {
	const q = `
SELECT s.token_hash, s.id, s.user_id, s.created_at, s.last_seen_at, s.expires_at,
       COALESCE(s.ip, ''), COALESCE(s.user_agent, ''), ` + userColumns + `
FROM sessions s
JOIN users u ON u.id = s.user_id
JOIN organizations o ON o.id = u.org_id
WHERE s.token_hash = $1;
`
	var r SessionWithUser
	u := &r.User
	err := s.pool.QueryRow(ctx, q, tokenHash).Scan(
		&r.TokenHash, &r.ID, &r.UserID, &r.CreatedAt, &r.LastSeenAt, &r.ExpiresAt, &r.IP, &r.UserAgent,
		&u.ID, &u.OrgID, &u.OrgName, &u.Email, &u.FullName, &u.RegistrationNo, &u.Roles,
		&u.Status, &u.PasswordHash, &u.FailedLogins, &u.LockedUntil, &u.CreatedAt, &u.LastLoginAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &r, nil
}

// TouchSession records activity on a session.
func (s *Store) TouchSession(ctx context.Context, tokenHash []byte, now time.Time) error {
	_, err := s.pool.Exec(ctx, `UPDATE sessions SET last_seen_at = $2 WHERE token_hash = $1`, tokenHash, now)
	return err
}

// DeleteSession removes one session (logout, or an expired session).
func (s *Store) DeleteSession(ctx context.Context, tokenHash []byte) error {
	_, err := s.pool.Exec(ctx, `DELETE FROM sessions WHERE token_hash = $1`, tokenHash)
	return err
}

// DeleteUserSessions removes every session of the account except the one
// whose hash is keep (nil keeps none).
func (s *Store) DeleteUserSessions(ctx context.Context, userID string, keep []byte) error {
	_, err := s.pool.Exec(ctx,
		`DELETE FROM sessions WHERE user_id = $1 AND ($2::bytea IS NULL OR token_hash <> $2)`, userID, keep)
	return err
}

// ListUserSessions returns the account's sessions, most recently used
// first. Expired ones may be among them until the next purge.
func (s *Store) ListUserSessions(ctx context.Context, userID string) ([]Session, error) {
	rows, err := s.pool.Query(ctx, `
SELECT token_hash, id, user_id, created_at, last_seen_at, expires_at, COALESCE(ip, ''), COALESCE(user_agent, '')
FROM sessions WHERE user_id = $1
ORDER BY last_seen_at DESC, created_at DESC`, userID)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(r pgx.CollectableRow) (Session, error) {
		var x Session
		err := r.Scan(&x.TokenHash, &x.ID, &x.UserID, &x.CreatedAt, &x.LastSeenAt, &x.ExpiresAt, &x.IP, &x.UserAgent)
		return x, err
	})
}

// DeleteUserSession removes one session by id, if it belongs to the
// account, and reports whether it did. A malformed id removes nothing.
func (s *Store) DeleteUserSession(ctx context.Context, userID, sessionID string) (bool, error) {
	tag, err := s.pool.Exec(ctx,
		`DELETE FROM sessions WHERE user_id = $1 AND id::text = $2`, userID, sessionID)
	if err != nil {
		return false, err
	}
	return tag.RowsAffected() > 0, nil
}

// DeleteExpiredSessions removes sessions past their absolute expiry or
// idle for longer than idle, and returns how many it removed.
func (s *Store) DeleteExpiredSessions(ctx context.Context, now time.Time, idle time.Duration) (int64, error) {
	tag, err := s.pool.Exec(ctx,
		`DELETE FROM sessions WHERE expires_at <= $1 OR last_seen_at <= $2`, now, now.Add(-idle))
	if err != nil {
		return 0, err
	}
	return tag.RowsAffected(), nil
}

// Purposes of a one-time token.
const (
	PurposeSetup = "setup" // first password, for an invited account
	PurposeReset = "reset" // a new password, issued by an admin
)

// OneTimeToken maps to the `one_time_tokens` table.
type OneTimeToken struct {
	TokenHash []byte
	UserID    string
	Purpose   string
	CreatedAt time.Time
	ExpiresAt time.Time
	CreatedBy *string
}

// CreateOneTimeToken stores a setup or reset token for an account and
// removes the account's earlier unused ones, so only the newest link works.
func (s *Store) CreateOneTimeToken(ctx context.Context, t OneTimeToken) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx,
			`DELETE FROM one_time_tokens WHERE user_id = $1 AND used_at IS NULL`, t.UserID); err != nil {
			return err
		}
		_, err := tx.Exec(ctx, `
INSERT INTO one_time_tokens (token_hash, user_id, purpose, created_at, expires_at, created_by)
VALUES ($1, $2, $3, $4, $5, $6)`, t.TokenHash, t.UserID, t.Purpose, t.CreatedAt, t.ExpiresAt, t.CreatedBy)
		return err
	})
}

// GetUsableToken returns the account a token can set the password of: the
// token is unused and unexpired at now, and the account isn't disabled.
// It returns nil otherwise.
func (s *Store) GetUsableToken(ctx context.Context, tokenHash []byte, now time.Time) (*User, error) {
	return scanUser(s.pool.QueryRow(ctx, `
SELECT `+userColumns+`
FROM one_time_tokens t
JOIN users u ON u.id = t.user_id
JOIN organizations o ON o.id = u.org_id
WHERE t.token_hash = $1 AND t.used_at IS NULL AND t.expires_at > $2 AND u.status <> 'disabled'`,
		tokenHash, now))
}

// ErrTokenUnusable means a setup or reset token is unknown, used, expired,
// or for a disabled account.
var ErrTokenUnusable = errors.New("this link is invalid or has expired")

// CompletePasswordSetup uses a setup or reset token: in one transaction it
// marks the token used, sets the password hash, activates an invited
// account, clears any lock and removes the account's sessions.
func (s *Store) CompletePasswordSetup(ctx context.Context, tokenHash []byte, passwordHash string, now time.Time) (string, error) {
	var userID string
	err := pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		err := tx.QueryRow(ctx, `
UPDATE one_time_tokens SET used_at = $2
WHERE token_hash = $1 AND used_at IS NULL AND expires_at > $2
RETURNING user_id`, tokenHash, now).Scan(&userID)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrTokenUnusable
		}
		if err != nil {
			return err
		}
		tag, err := tx.Exec(ctx, `
UPDATE users SET
    password_hash = $2, password_changed_at = $3,
    status = CASE WHEN status = 'invited' THEN 'active' ELSE status END,
    failed_logins = 0, locked_until = NULL
WHERE id = $1 AND status <> 'disabled'`, userID, passwordHash, now)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			return ErrTokenUnusable
		}
		_, err = tx.Exec(ctx, `DELETE FROM sessions WHERE user_id = $1`, userID)
		return err
	})
	if err != nil {
		return "", err
	}
	return userID, nil
}
