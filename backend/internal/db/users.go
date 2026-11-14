package db

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

// Organization maps to the `organizations` table.
type Organization struct {
	ID        string
	Name      string
	CreatedAt time.Time
}

// Account statuses.
const (
	StatusInvited  = "invited"  // created; no password set yet
	StatusActive   = "active"   // can log in
	StatusDisabled = "disabled" // can't log in; sessions removed
)

// Roles.
const (
	RoleClinician = "clinician"
	RoleAdmin     = "admin"
)

// User maps to the `staff_users` table: a staff account.
type User struct {
	ID             string
	OrgID          string
	OrgName        string // filled by the queries that join organizations
	Email          string
	FullName       string
	RegistrationNo *string
	Roles          []string
	Status         string
	PasswordHash   *string
	FailedLogins   int
	LockedUntil    *time.Time
	CreatedAt      time.Time
	LastLoginAt    *time.Time
}

// ErrEmailTaken means an account with that email already exists.
var ErrEmailTaken = errors.New("an account with this email already exists")

// ErrOrgNameTaken means an organisation with that name already exists.
var ErrOrgNameTaken = errors.New("an organisation with this name already exists")

func isUniqueViolation(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "23505"
}

// CreateOrganization inserts an organisation.
func (s *Store) CreateOrganization(ctx context.Context, name string) (*Organization, error) {
	var o Organization
	err := s.pool.QueryRow(ctx,
		`INSERT INTO organizations (name) VALUES ($1) RETURNING id, name, created_at`, name,
	).Scan(&o.ID, &o.Name, &o.CreatedAt)
	if isUniqueViolation(err) {
		return nil, ErrOrgNameTaken
	}
	if err != nil {
		return nil, err
	}
	return &o, nil
}

// ListOrganizations returns every organisation, oldest first.
func (s *Store) ListOrganizations(ctx context.Context) ([]Organization, error) {
	rows, err := s.pool.Query(ctx, `SELECT id, name, created_at FROM organizations ORDER BY created_at, name`)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(r pgx.CollectableRow) (Organization, error) {
		var o Organization
		err := r.Scan(&o.ID, &o.Name, &o.CreatedAt)
		return o, err
	})
}

// NewUser is what creating an account needs. The email must already be
// normalised (auth.NormalizeEmail).
type NewUser struct {
	OrgID          string
	Email          string
	FullName       string
	RegistrationNo *string
	Roles          []string
	CreatedBy      *string
}

const userColumns = `u.id, u.org_id, o.name, u.email, u.full_name, u.registration_no, u.roles,
       u.status, u.password_hash, u.failed_logins, u.locked_until, u.created_at, u.last_login_at`

func scanUser(row pgx.Row) (*User, error) {
	var u User
	err := row.Scan(&u.ID, &u.OrgID, &u.OrgName, &u.Email, &u.FullName, &u.RegistrationNo, &u.Roles,
		&u.Status, &u.PasswordHash, &u.FailedLogins, &u.LockedUntil, &u.CreatedAt, &u.LastLoginAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &u, nil
}

// CreateUser inserts an account in status "invited", with no password,
// and writes ev (with the account as its target) in the same transaction.
func (s *Store) CreateUser(ctx context.Context, nu NewUser, ev *AuditEvent) (*User, error) {
	const q = `
WITH u AS (
    INSERT INTO staff_users (org_id, email, full_name, registration_no, roles, created_by)
    VALUES ($1, $2, $3, $4, $5, $6)
    RETURNING *
)
SELECT ` + userColumns + ` FROM u JOIN organizations o ON o.id = u.org_id;
`
	var u *User
	err := pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		var err error
		u, err = scanUser(tx.QueryRow(ctx, q, nu.OrgID, nu.Email, nu.FullName, nu.RegistrationNo, nu.Roles, nu.CreatedBy))
		if err != nil {
			return err
		}
		return writeAudit(ctx, tx, ev.about(u.ID, ""))
	})
	if isUniqueViolation(err) {
		return nil, ErrEmailTaken
	}
	if err != nil {
		return nil, err
	}
	return u, nil
}

// GetUserByEmail returns the account with that (normalised) email, or nil.
func (s *Store) GetUserByEmail(ctx context.Context, email string) (*User, error) {
	return scanUser(s.pool.QueryRow(ctx,
		`SELECT `+userColumns+` FROM staff_users u JOIN organizations o ON o.id = u.org_id WHERE u.email = $1`, email))
}

// GetUserByID returns the account, or nil.
func (s *Store) GetUserByID(ctx context.Context, id string) (*User, error) {
	return scanUser(s.pool.QueryRow(ctx,
		`SELECT `+userColumns+` FROM staff_users u JOIN organizations o ON o.id = u.org_id WHERE u.id = $1`, id))
}

// RecordLoginFailure counts a failed password for the account. The
// maxFailures-th failure in a row locks the account until lockUntil and
// starts the count again. It returns the lock time, if one is set. ev
// is written in the same transaction, and when this failure locks the
// account a copy of it with the action ActionAccountLocked as well.
func (s *Store) RecordLoginFailure(ctx context.Context, userID string, maxFailures int, lockUntil time.Time, ev *AuditEvent) (*time.Time, error) {
	const q = `
UPDATE staff_users SET
    failed_logins = CASE WHEN failed_logins + 1 >= $2 THEN 0 ELSE failed_logins + 1 END,
    locked_until  = CASE WHEN failed_logins + 1 >= $2 THEN $3 ELSE locked_until END
WHERE id = $1
RETURNING locked_until;
`
	var locked *time.Time
	err := pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if err := tx.QueryRow(ctx, q, userID, maxFailures, lockUntil).Scan(&locked); err != nil {
			return err
		}
		if err := writeAudit(ctx, tx, ev); err != nil {
			return err
		}
		if ev != nil && locked != nil && locked.Equal(lockUntil) {
			lock := *ev
			lock.Action = ActionAccountLocked
			return writeAudit(ctx, tx, &lock)
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return locked, nil
}

// RecordLoginSuccess clears the failure count and lock and sets
// last_login_at, and writes ev in the same transaction.
func (s *Store) RecordLoginSuccess(ctx context.Context, userID string, now time.Time, ev *AuditEvent) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx,
			`UPDATE staff_users SET failed_logins = 0, locked_until = NULL, last_login_at = $2 WHERE id = $1`,
			userID, now); err != nil {
			return err
		}
		return writeAudit(ctx, tx, ev)
	})
}

// SetPasswordHash replaces the account's password hash and writes ev in
// the same transaction (nil for a rehash at login, which changes nothing
// the owner chose).
func (s *Store) SetPasswordHash(ctx context.Context, userID, hash string, now time.Time, ev *AuditEvent) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx,
			`UPDATE staff_users SET password_hash = $2, password_changed_at = $3 WHERE id = $1`,
			userID, hash, now); err != nil {
			return err
		}
		return writeAudit(ctx, tx, ev)
	})
}

// SetUserStatus sets the account's status. Disabling an account removes
// its sessions in the same transaction, as is ev.
func (s *Store) SetUserStatus(ctx context.Context, userID, status string, ev *AuditEvent) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `UPDATE staff_users SET status = $2 WHERE id = $1`, userID, status); err != nil {
			return err
		}
		if status == StatusDisabled {
			if _, err := tx.Exec(ctx, `DELETE FROM sessions WHERE user_id = $1`, userID); err != nil {
				return err
			}
		}
		return writeAudit(ctx, tx, ev)
	})
}
