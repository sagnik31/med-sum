package db

import (
	"context"
	"errors"
	"slices"
	"testing"
	"time"
)

// mustAdmin creates an active admin in the test organisation.
func mustAdmin(t *testing.T, s *Store, email string) *User {
	t.Helper()
	u, err := s.CreateUser(context.Background(), NewUser{
		OrgID: mustOrg(t, s), Email: email, FullName: "Admin " + email, Roles: []string{RoleAdmin},
	})
	if err != nil {
		t.Fatal(err)
	}
	activate(t, s, u.ID)
	return u
}

func mustSession(t *testing.T, s *Store, token, userID string, lastSeen time.Time) {
	t.Helper()
	if err := s.CreateSession(context.Background(), Session{TokenHash: hashOf(token), UserID: userID,
		CreatedAt: t0, LastSeenAt: lastSeen, ExpiresAt: t0.Add(12 * time.Hour)}); err != nil {
		t.Fatal(err)
	}
}

func sessionTokens(t *testing.T, s *Store, userID string) int {
	t.Helper()
	sessions, err := s.ListUserSessions(context.Background(), userID)
	if err != nil {
		t.Fatal(err)
	}
	return len(sessions)
}

func TestListUsersStaysInTheOrganisation(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	mustUser(t, s, "zed@hospital.test")
	mustUser(t, s, "amy@hospital.test")
	other, err := s.CreateOrganization(ctx, "Other Hospital")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.CreateUser(ctx, NewUser{OrgID: other.ID, Email: "x@other.test", FullName: "X",
		Roles: []string{RoleClinician}}); err != nil {
		t.Fatal(err)
	}

	users, err := s.ListUsers(ctx, mustOrg(t, s))
	if err != nil {
		t.Fatal(err)
	}
	if len(users) != 2 || users[0].Email != "amy@hospital.test" || users[1].Email != "zed@hospital.test" ||
		users[0].OrgName != "Test Hospital" {
		t.Errorf("users = %+v", users)
	}
}

func TestUpdateUserChangesFieldsAndEndsSessionsOnARoleChange(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	org := mustOrg(t, s)
	u := mustUser(t, s, "asha@hospital.test")
	activate(t, s, u.ID)
	mustSession(t, s, "a", u.ID, t0)
	mustSession(t, s, "b", u.ID, t0)

	// Name and registration number alone keep the sessions.
	got, err := s.UpdateUser(ctx, UserUpdate{OrgID: org, UserID: u.ID, FullName: strPtr("Dr Asha R"),
		RegistrationNo: strPtr("MCI-1")})
	if err != nil {
		t.Fatal(err)
	}
	if got.FullName != "Dr Asha R" || got.RegistrationNo == nil || *got.RegistrationNo != "MCI-1" ||
		got.OrgName != "Test Hospital" {
		t.Errorf("user = %+v", got)
	}
	if n := sessionTokens(t, s, u.ID); n != 2 {
		t.Errorf("%d sessions after a name change, want 2", n)
	}

	// A role change ends them, except the kept one.
	got, err = s.UpdateUser(ctx, UserUpdate{OrgID: org, UserID: u.ID, Roles: []string{RoleClinician, RoleAdmin},
		RegistrationNo: strPtr(""), KeepSession: hashOf("b")})
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(got.Roles, []string{RoleClinician, RoleAdmin}) || got.RegistrationNo != nil {
		t.Errorf("user = %+v", got)
	}
	if sess, _ := s.GetSession(ctx, hashOf("b")); sess == nil {
		t.Error("the kept session was removed")
	}
	if sess, _ := s.GetSession(ctx, hashOf("a")); sess != nil {
		t.Error("the other session survived a role change")
	}

	// Setting the same roles again is not a change.
	if _, err := s.UpdateUser(ctx, UserUpdate{OrgID: org, UserID: u.ID, Roles: []string{RoleClinician, RoleAdmin}}); err != nil {
		t.Fatal(err)
	}
	if n := sessionTokens(t, s, u.ID); n != 1 {
		t.Errorf("%d sessions, want 1", n)
	}
}

func TestUpdateUserDisablesAndEnables(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	org := mustOrg(t, s)
	active := mustUser(t, s, "asha@hospital.test")
	activate(t, s, active.ID)
	invited := mustUser(t, s, "new@hospital.test")
	mustSession(t, s, "a", active.ID, t0)
	for i, id := range []string{active.ID, invited.ID} {
		if err := s.CreateOneTimeToken(ctx, OneTimeToken{TokenHash: hashOf("link" + string(rune('0'+i))), UserID: id,
			Purpose: PurposeReset, CreatedAt: t0, ExpiresAt: t0.Add(time.Hour)}); err != nil {
			t.Fatal(err)
		}
	}

	disabled, enabled := StatusDisabled, StatusActive
	for _, id := range []string{active.ID, invited.ID} {
		got, err := s.UpdateUser(ctx, UserUpdate{OrgID: org, UserID: id, Status: &disabled})
		if err != nil || got.Status != StatusDisabled {
			t.Fatalf("disable: %+v, %v", got, err)
		}
	}
	if n := sessionTokens(t, s, active.ID); n != 0 {
		t.Errorf("%d sessions after disabling", n)
	}
	var links int
	if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM one_time_tokens`).Scan(&links); err != nil || links != 0 {
		t.Errorf("%d unused links after disabling, %v", links, err)
	}

	// Enabled again: the account with a password is active, the one
	// without is invited (the schema forbids an active account with no
	// password).
	got, err := s.UpdateUser(ctx, UserUpdate{OrgID: org, UserID: active.ID, Status: &enabled})
	if err != nil || got.Status != StatusActive {
		t.Errorf("enable active: %+v, %v", got, err)
	}
	got, err = s.UpdateUser(ctx, UserUpdate{OrgID: org, UserID: invited.ID, Status: &enabled})
	if err != nil || got.Status != StatusInvited {
		t.Errorf("enable invited: %+v, %v", got, err)
	}
}

func TestUpdateUserKeepsAnActiveAdmin(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	org := mustOrg(t, s)
	first := mustAdmin(t, s, "first@hospital.test")
	disabled := StatusDisabled

	for name, upd := range map[string]UserUpdate{
		"disable":     {OrgID: org, UserID: first.ID, Status: &disabled},
		"demote":      {OrgID: org, UserID: first.ID, Roles: []string{RoleClinician}},
		"demote+name": {OrgID: org, UserID: first.ID, Roles: []string{RoleClinician}, FullName: strPtr("Changed")},
	} {
		if _, err := s.UpdateUser(ctx, upd); !errors.Is(err, ErrLastAdmin) {
			t.Errorf("%s: err = %v, want ErrLastAdmin", name, err)
		}
	}
	if u, _ := s.GetUserByID(ctx, first.ID); u.Status != StatusActive || u.FullName == "Changed" {
		t.Errorf("the refused change was partly applied: %+v", u)
	}

	// An invited admin can't log in, so doesn't count.
	invited, err := s.CreateUser(ctx, NewUser{OrgID: org, Email: "invited@hospital.test", FullName: "I",
		Roles: []string{RoleAdmin}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.UpdateUser(ctx, UserUpdate{OrgID: org, UserID: first.ID, Status: &disabled}); !errors.Is(err, ErrLastAdmin) {
		t.Errorf("with only an invited second admin: err = %v", err)
	}
	// Demoting the invited one is fine: it wasn't an active admin.
	if _, err := s.UpdateUser(ctx, UserUpdate{OrgID: org, UserID: invited.ID, Roles: []string{RoleClinician}}); err != nil {
		t.Errorf("demote invited admin: %v", err)
	}

	// With a second active admin, the first can go.
	mustAdmin(t, s, "second@hospital.test")
	if _, err := s.UpdateUser(ctx, UserUpdate{OrgID: org, UserID: first.ID, Status: &disabled}); err != nil {
		t.Errorf("with two admins: %v", err)
	}
}

func TestUpdateUserIgnoresOtherOrganisations(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	u := mustUser(t, s, "asha@hospital.test")
	other, err := s.CreateOrganization(ctx, "Other Hospital")
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{u.ID, "not-a-uuid", "00000000-0000-4000-8000-000000000000"} {
		got, err := s.UpdateUser(ctx, UserUpdate{OrgID: other.ID, UserID: id, FullName: strPtr("Hijacked")})
		if got != nil || err != nil {
			t.Errorf("%s: %+v, %v; want nil, nil", id, got, err)
		}
	}
	if got, _ := s.GetUserByID(ctx, u.ID); got.FullName == "Hijacked" {
		t.Error("another organisation's admin renamed the account")
	}
}

func TestListAndDeleteUserSessions(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "asha@hospital.test")
	other := mustUser(t, s, "ravi@hospital.test")
	mustSession(t, s, "old", me.ID, t0)
	mustSession(t, s, "new", me.ID, t0.Add(time.Minute))
	mustSession(t, s, "theirs", other.ID, t0)

	sessions, err := s.ListUserSessions(ctx, me.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(sessions) != 2 || string(sessions[0].TokenHash) != string(hashOf("new")) || sessions[0].ID == "" ||
		sessions[0].ID == sessions[1].ID {
		t.Fatalf("sessions = %+v", sessions)
	}
	if got, _ := s.GetSession(ctx, hashOf("new")); got.ID != sessions[0].ID {
		t.Errorf("GetSession id = %q, want %q", got.ID, sessions[0].ID)
	}

	theirs, _ := s.ListUserSessions(ctx, other.ID)
	for _, id := range []string{theirs[0].ID, "not-a-uuid"} {
		if ok, err := s.DeleteUserSession(ctx, me.ID, id); ok || err != nil {
			t.Errorf("delete %q as me: %v, %v; want false, nil", id, ok, err)
		}
	}
	if ok, err := s.DeleteUserSession(ctx, me.ID, sessions[1].ID); !ok || err != nil {
		t.Errorf("delete mine: %v, %v", ok, err)
	}
	if n := sessionTokens(t, s, me.ID); n != 1 {
		t.Errorf("%d sessions left, want 1", n)
	}
	if n := sessionTokens(t, s, other.ID); n != 1 {
		t.Errorf("their session was removed")
	}
}
