package httpapi

import (
	"context"
	"slices"
	"sort"
	"strings"

	"backend/internal/db"
)

// The fake store's staff and session-list methods, which mirror
// internal/db/staff.go and internal/db/sessions.go.

func (f *fakeStore) ListUserSessions(_ context.Context, userID string) ([]db.Session, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	out := []db.Session{}
	for _, sess := range f.sessions {
		if sess.UserID == userID {
			out = append(out, *sess)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].LastSeenAt.After(out[j].LastSeenAt) })
	return out, nil
}

func (f *fakeStore) DeleteUserSession(_ context.Context, userID, sessionID string) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return false, f.failWith
	}
	for k, sess := range f.sessions {
		if sess.UserID == userID && sess.ID == sessionID {
			delete(f.sessions, k)
			return true, nil
		}
	}
	return false, nil
}

func (f *fakeStore) CreateOneTimeToken(_ context.Context, t db.OneTimeToken) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return f.failWith
	}
	for k, old := range f.tokens {
		if old.UserID == t.UserID && old.UsedAt == nil {
			delete(f.tokens, k)
		}
	}
	f.tokens[string(t.TokenHash)] = &fakeToken{OneTimeToken: t}
	return nil
}

// tokensOf counts the account's unused one-time tokens.
func (f *fakeStore) tokensOf(userID string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := 0
	for _, t := range f.tokens {
		if t.UserID == userID && t.UsedAt == nil {
			n++
		}
	}
	return n
}

func (f *fakeStore) ListUsers(_ context.Context, orgID string) ([]db.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	out := []db.User{}
	for _, u := range f.users {
		if u.OrgID == orgID {
			out = append(out, *u)
		}
	}
	sort.Slice(out, func(i, j int) bool { return strings.ToLower(out[i].FullName) < strings.ToLower(out[j].FullName) })
	return out, nil
}

func (f *fakeStore) CreateUser(_ context.Context, nu db.NewUser) (*db.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	orgName := ""
	for _, u := range f.users {
		if u.Email == nu.Email {
			return nil, db.ErrEmailTaken
		}
		if u.OrgID == nu.OrgID {
			orgName = u.OrgName
		}
	}
	u := &db.User{ID: f.newID(), OrgID: nu.OrgID, OrgName: orgName, Email: nu.Email, FullName: nu.FullName,
		RegistrationNo: nu.RegistrationNo, Roles: nu.Roles, Status: db.StatusInvited}
	f.users[u.ID] = u
	copied := *u
	return &copied, nil
}

func fakeActiveAdmin(status string, roles []string) bool {
	return status == db.StatusActive && slices.Contains(roles, db.RoleAdmin)
}

func (f *fakeStore) UpdateUser(_ context.Context, upd db.UserUpdate) (*db.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	cur, ok := f.users[upd.UserID]
	if !ok || cur.OrgID != upd.OrgID {
		return nil, nil
	}
	roles, status := cur.Roles, cur.Status
	if upd.Roles != nil {
		roles = upd.Roles
	}
	if upd.Status != nil {
		status = *upd.Status
		if status == db.StatusActive && cur.PasswordHash == nil {
			status = db.StatusInvited
		}
	}
	if fakeActiveAdmin(cur.Status, cur.Roles) && !fakeActiveAdmin(status, roles) {
		others := 0
		for _, u := range f.users {
			if u.OrgID == cur.OrgID && u.ID != cur.ID && fakeActiveAdmin(u.Status, u.Roles) {
				others++
			}
		}
		if others == 0 {
			return nil, db.ErrLastAdmin
		}
	}
	changed := status != cur.Status || !slices.Equal(roles, cur.Roles)
	if status == db.StatusDisabled && cur.Status != db.StatusDisabled {
		for k, t := range f.tokens {
			if t.UserID == cur.ID && t.UsedAt == nil {
				delete(f.tokens, k)
			}
		}
	}
	cur.Roles, cur.Status = roles, status
	if upd.FullName != nil {
		cur.FullName = *upd.FullName
	}
	if upd.RegistrationNo != nil {
		cur.RegistrationNo = upd.RegistrationNo
		if *upd.RegistrationNo == "" {
			cur.RegistrationNo = nil
		}
	}
	if changed {
		for k, sess := range f.sessions {
			if sess.UserID == cur.ID && (upd.KeepSession == nil || k != string(upd.KeepSession)) {
				delete(f.sessions, k)
			}
		}
	}
	copied := *cur
	return &copied, nil
}
