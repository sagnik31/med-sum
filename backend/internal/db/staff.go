package db

import (
	"context"
	"errors"
	"slices"

	"github.com/jackc/pgx/v5"
)

// ErrLastAdmin means a change would leave an organisation with no active
// admin, so nobody could manage its staff.
var ErrLastAdmin = errors.New("this would leave the organisation without an active admin")

// ListUsers returns the organisation's accounts, by name.
func (s *Store) ListUsers(ctx context.Context, orgID string) ([]User, error) {
	rows, err := s.pool.Query(ctx, `SELECT `+userColumns+`
FROM users u JOIN organizations o ON o.id = u.org_id
WHERE u.org_id = $1
ORDER BY lower(u.full_name), u.email`, orgID)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(r pgx.CollectableRow) (User, error) {
		u, err := scanUser(r)
		if err != nil {
			return User{}, err
		}
		return *u, nil
	})
}

// UserUpdate is an admin's change to an account. Nil fields stay as they
// are. Status may be StatusActive or StatusDisabled; re-enabling an
// account that never set a password makes it StatusInvited again. An
// empty RegistrationNo clears it.
type UserUpdate struct {
	OrgID          string // the admin's organisation; other accounts are not found
	UserID         string
	Roles          []string
	Status         *string
	FullName       *string
	RegistrationNo *string
	// KeepSession is a session of the account that survives the change
	// (the admin's own, when they change their own account), or nil.
	KeepSession []byte
}

func isActiveAdmin(status string, roles []string) bool {
	return status == StatusActive && slices.Contains(roles, RoleAdmin)
}

// UpdateUser applies the change in one transaction and returns the
// account, or nil if the organisation has no such account. A change to
// the roles or status ends the account's sessions (except KeepSession), and
// disabling it also removes its unused setup and reset links. It
// returns ErrLastAdmin, changing nothing, if the organisation would be
// left without an active admin.
func (s *Store) UpdateUser(ctx context.Context, upd UserUpdate) (*User, error) {
	var out *User
	err := pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		// The organisation row serialises staff changes, so two admins
		// demoting each other at once can't both succeed.
		if _, err := tx.Exec(ctx, `SELECT 1 FROM organizations WHERE id = $1 FOR UPDATE`, upd.OrgID); err != nil {
			return err
		}
		cur, err := scanUser(tx.QueryRow(ctx, `SELECT `+userColumns+`
FROM users u JOIN organizations o ON o.id = u.org_id
WHERE u.id::text = $1 AND u.org_id = $2`, upd.UserID, upd.OrgID))
		if err != nil || cur == nil {
			return err
		}

		roles, status := cur.Roles, cur.Status
		if upd.Roles != nil {
			roles = upd.Roles
		}
		if upd.Status != nil {
			status = *upd.Status
			if status == StatusActive && cur.PasswordHash == nil {
				status = StatusInvited
			}
		}
		if isActiveAdmin(cur.Status, cur.Roles) && !isActiveAdmin(status, roles) {
			var others int
			if err := tx.QueryRow(ctx, `
SELECT count(*) FROM users
WHERE org_id = $1 AND id <> $2 AND status = 'active' AND 'admin' = ANY (roles)`,
				upd.OrgID, cur.ID).Scan(&others); err != nil {
				return err
			}
			if others == 0 {
				return ErrLastAdmin
			}
		}

		fullName := cur.FullName
		if upd.FullName != nil {
			fullName = *upd.FullName
		}
		regNo := cur.RegistrationNo
		if upd.RegistrationNo != nil {
			regNo = upd.RegistrationNo
			if *regNo == "" {
				regNo = nil
			}
		}
		if _, err := tx.Exec(ctx, `
UPDATE users SET roles = $2, status = $3, full_name = $4, registration_no = $5 WHERE id = $1`,
			cur.ID, roles, status, fullName, regNo); err != nil {
			return err
		}
		if status != cur.Status || !slices.Equal(roles, cur.Roles) {
			if _, err := tx.Exec(ctx,
				`DELETE FROM sessions WHERE user_id = $1 AND ($2::bytea IS NULL OR token_hash <> $2)`,
				cur.ID, upd.KeepSession); err != nil {
				return err
			}
		}
		// A disabled account's unused links die with it, so enabling it
		// again doesn't bring an old link back to life.
		if status == StatusDisabled && cur.Status != StatusDisabled {
			if _, err := tx.Exec(ctx,
				`DELETE FROM one_time_tokens WHERE user_id = $1 AND used_at IS NULL`, cur.ID); err != nil {
				return err
			}
		}
		out, err = scanUser(tx.QueryRow(ctx, `SELECT `+userColumns+`
FROM users u JOIN organizations o ON o.id = u.org_id WHERE u.id = $1`, cur.ID))
		return err
	})
	if err != nil {
		return nil, err
	}
	return out, nil
}
