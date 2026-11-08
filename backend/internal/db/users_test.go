package db

import (
	"context"
	"crypto/sha256"
	"errors"
	"testing"
	"time"
)

var t0 = time.Date(2026, 11, 8, 9, 0, 0, 0, time.UTC)

func hashOf(s string) []byte {
	sum := sha256.Sum256([]byte(s))
	return sum[:]
}

func activate(t *testing.T, s *Store, id string) {
	t.Helper()
	ctx := context.Background()
	if err := s.SetPasswordHash(ctx, id, "$argon2id$v=19$m=64,t=1,p=1$c2FsdA$a2V5", t0); err != nil {
		t.Fatal(err)
	}
	if err := s.SetUserStatus(ctx, id, StatusActive); err != nil {
		t.Fatal(err)
	}
}

func TestCreateUserThenFindIt(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	reg := "KMC-12345"
	org := mustOrg(t, s)

	u, err := s.CreateUser(ctx, NewUser{OrgID: org, Email: "asha@hospital.test", FullName: "Dr Asha Rao",
		RegistrationNo: &reg, Roles: []string{RoleClinician, RoleAdmin}})
	if err != nil {
		t.Fatal(err)
	}
	if u.Status != StatusInvited || u.PasswordHash != nil || u.OrgName != "Test Hospital" ||
		len(u.Roles) != 2 || u.RegistrationNo == nil || *u.RegistrationNo != reg {
		t.Errorf("created = %+v", u)
	}

	byEmail, err := s.GetUserByEmail(ctx, "asha@hospital.test")
	if err != nil || byEmail == nil || byEmail.ID != u.ID {
		t.Errorf("GetUserByEmail = %+v, %v", byEmail, err)
	}
	byID, err := s.GetUserByID(ctx, u.ID)
	if err != nil || byID == nil || byID.Email != u.Email {
		t.Errorf("GetUserByID = %+v, %v", byID, err)
	}
	if missing, err := s.GetUserByEmail(ctx, "nobody@hospital.test"); missing != nil || err != nil {
		t.Errorf("unknown email = %+v, %v; want nil, nil", missing, err)
	}
}

func TestCreateUserRefusesADuplicateEmail(t *testing.T) {
	s := store(t)
	mustUser(t, s, "asha@hospital.test")

	_, err := s.CreateUser(context.Background(), NewUser{OrgID: mustOrg(t, s), Email: "asha@hospital.test",
		FullName: "Someone Else", Roles: []string{RoleClinician}})

	if !errors.Is(err, ErrEmailTaken) {
		t.Errorf("error = %v, want ErrEmailTaken", err)
	}
}

func TestCreateOrganizationRefusesADuplicateName(t *testing.T) {
	s := store(t)
	mustOrg(t, s)
	if _, err := s.CreateOrganization(context.Background(), "Test Hospital"); !errors.Is(err, ErrOrgNameTaken) {
		t.Errorf("error = %v, want ErrOrgNameTaken", err)
	}
}

func TestUserConstraints(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	org := mustOrg(t, s)
	bad := map[string]NewUser{
		"upper-case email": {OrgID: org, Email: "Asha@hospital.test", FullName: "A", Roles: []string{RoleClinician}},
		"not an email":     {OrgID: org, Email: "asha", FullName: "A", Roles: []string{RoleClinician}},
		"empty name":       {OrgID: org, Email: "a@hospital.test", FullName: "", Roles: []string{RoleClinician}},
		"no roles":         {OrgID: org, Email: "b@hospital.test", FullName: "B", Roles: []string{}},
		"unknown role":     {OrgID: org, Email: "c@hospital.test", FullName: "C", Roles: []string{"superuser"}},
	}
	for name, nu := range bad {
		t.Run(name, func(t *testing.T) {
			if _, err := s.CreateUser(ctx, nu); err == nil {
				t.Error("the row was accepted")
			}
		})
	}
	// An account can't be active without a password.
	u := mustUser(t, s, "d@hospital.test")
	if err := s.SetUserStatus(ctx, u.ID, StatusActive); err == nil {
		t.Error("an account without a password became active")
	}
}

func TestLoginFailuresLockAfterTheLimit(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	u := mustUser(t, s, "asha@hospital.test")
	lock := t0.Add(15 * time.Minute)

	for i := 1; i <= 4; i++ {
		locked, err := s.RecordLoginFailure(ctx, u.ID, 5, lock)
		if err != nil || locked != nil {
			t.Fatalf("failure %d: locked = %v, err = %v", i, locked, err)
		}
	}
	locked, err := s.RecordLoginFailure(ctx, u.ID, 5, lock)
	if err != nil || locked == nil || !locked.Equal(lock) {
		t.Fatalf("5th failure: locked = %v, err = %v; want %v", locked, err, lock)
	}
	got, _ := s.GetUserByID(ctx, u.ID)
	if got.FailedLogins != 0 {
		t.Errorf("failed_logins = %d after the lock, want 0 (counting starts again)", got.FailedLogins)
	}

	if err := s.RecordLoginSuccess(ctx, u.ID, t0.Add(time.Hour)); err != nil {
		t.Fatal(err)
	}
	got, _ = s.GetUserByID(ctx, u.ID)
	if got.LockedUntil != nil || got.LastLoginAt == nil || !got.LastLoginAt.Equal(t0.Add(time.Hour)) {
		t.Errorf("after a success: %+v", got)
	}
}

func TestSessionLifecycle(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	u := mustUser(t, s, "asha@hospital.test")
	activate(t, s, u.ID)
	sess := Session{TokenHash: hashOf("a"), UserID: u.ID, CreatedAt: t0, LastSeenAt: t0,
		ExpiresAt: t0.Add(12 * time.Hour), IP: "10.0.0.1", UserAgent: "browser"}

	if err := s.CreateSession(ctx, sess); err != nil {
		t.Fatal(err)
	}
	got, err := s.GetSession(ctx, sess.TokenHash)
	if err != nil || got == nil {
		t.Fatalf("GetSession = %+v, %v", got, err)
	}
	if got.User.ID != u.ID || got.User.Status != StatusActive || got.User.OrgName != "Test Hospital" ||
		got.IP != "10.0.0.1" || !got.ExpiresAt.Equal(sess.ExpiresAt) {
		t.Errorf("session = %+v", got)
	}

	if err := s.TouchSession(ctx, sess.TokenHash, t0.Add(5*time.Minute)); err != nil {
		t.Fatal(err)
	}
	got, _ = s.GetSession(ctx, sess.TokenHash)
	if !got.LastSeenAt.Equal(t0.Add(5 * time.Minute)) {
		t.Errorf("last_seen_at = %v after a touch", got.LastSeenAt)
	}

	if err := s.DeleteSession(ctx, sess.TokenHash); err != nil {
		t.Fatal(err)
	}
	if got, err := s.GetSession(ctx, sess.TokenHash); got != nil || err != nil {
		t.Errorf("after delete: %+v, %v", got, err)
	}
}

func TestDeleteUserSessionsKeepsOne(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	me := mustUser(t, s, "asha@hospital.test")
	other := mustUser(t, s, "ravi@hospital.test")
	for _, x := range []struct{ token, user string }{{"a", me.ID}, {"b", me.ID}, {"c", me.ID}, {"d", other.ID}} {
		if err := s.CreateSession(ctx, Session{TokenHash: hashOf(x.token), UserID: x.user, CreatedAt: t0,
			LastSeenAt: t0, ExpiresAt: t0.Add(time.Hour)}); err != nil {
			t.Fatal(err)
		}
	}

	if err := s.DeleteUserSessions(ctx, me.ID, hashOf("b")); err != nil {
		t.Fatal(err)
	}
	for token, want := range map[string]bool{"a": false, "b": true, "c": false, "d": true} {
		got, _ := s.GetSession(ctx, hashOf(token))
		if (got != nil) != want {
			t.Errorf("session %s exists = %v, want %v", token, got != nil, want)
		}
	}

	if err := s.DeleteUserSessions(ctx, me.ID, nil); err != nil {
		t.Fatal(err)
	}
	if got, _ := s.GetSession(ctx, hashOf("b")); got != nil {
		t.Error("keep=nil kept a session")
	}
}

func TestDisablingAnAccountRemovesItsSessions(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	u := mustUser(t, s, "asha@hospital.test")
	activate(t, s, u.ID)
	_ = s.CreateSession(ctx, Session{TokenHash: hashOf("a"), UserID: u.ID, CreatedAt: t0, LastSeenAt: t0,
		ExpiresAt: t0.Add(time.Hour)})

	if err := s.SetUserStatus(ctx, u.ID, StatusDisabled); err != nil {
		t.Fatal(err)
	}
	if got, _ := s.GetSession(ctx, hashOf("a")); got != nil {
		t.Error("a disabled account kept its session")
	}
}

func TestDeleteExpiredSessions(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	u := mustUser(t, s, "asha@hospital.test")
	now := t0.Add(13 * time.Hour)
	rows := map[string]Session{
		"live":         {CreatedAt: now.Add(-time.Hour), LastSeenAt: now.Add(-10 * time.Minute), ExpiresAt: now.Add(11 * time.Hour)},
		"idle":         {CreatedAt: now.Add(-time.Hour), LastSeenAt: now.Add(-31 * time.Minute), ExpiresAt: now.Add(11 * time.Hour)},
		"past expiry":  {CreatedAt: t0, LastSeenAt: now.Add(-time.Minute), ExpiresAt: t0.Add(12 * time.Hour)},
		"expires now!": {CreatedAt: t0, LastSeenAt: now.Add(-time.Minute), ExpiresAt: now},
	}
	for token, sess := range rows {
		sess.TokenHash, sess.UserID = hashOf(token), u.ID
		if err := s.CreateSession(ctx, sess); err != nil {
			t.Fatal(err)
		}
	}

	n, err := s.DeleteExpiredSessions(ctx, now, 30*time.Minute)

	if err != nil || n != 3 {
		t.Fatalf("removed %d (%v), want 3", n, err)
	}
	if got, _ := s.GetSession(ctx, hashOf("live")); got == nil {
		t.Error("the live session was removed")
	}
}

func TestSessionTokenHashMustBe32Bytes(t *testing.T) {
	s := store(t)
	u := mustUser(t, s, "asha@hospital.test")
	err := s.CreateSession(context.Background(), Session{TokenHash: []byte("raw token"), UserID: u.ID,
		CreatedAt: t0, LastSeenAt: t0, ExpiresAt: t0.Add(time.Hour)})
	if err == nil {
		t.Error("a session keyed by something other than a SHA-256 was accepted")
	}
}

func TestOneTimeTokenSetsThePasswordOnce(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	u := mustUser(t, s, "asha@hospital.test")
	_ = s.CreateSession(ctx, Session{TokenHash: hashOf("old"), UserID: u.ID, CreatedAt: t0, LastSeenAt: t0,
		ExpiresAt: t0.Add(time.Hour)})
	tok := OneTimeToken{TokenHash: hashOf("setup"), UserID: u.ID, Purpose: PurposeSetup, CreatedAt: t0,
		ExpiresAt: t0.Add(72 * time.Hour)}
	if err := s.CreateOneTimeToken(ctx, tok); err != nil {
		t.Fatal(err)
	}

	usable, err := s.GetUsableToken(ctx, tok.TokenHash, t0.Add(time.Hour))
	if err != nil || usable == nil || usable.ID != u.ID {
		t.Fatalf("GetUsableToken = %+v, %v", usable, err)
	}
	id, err := s.CompletePasswordSetup(ctx, tok.TokenHash, "$argon2id$v=19$m=64,t=1,p=1$c2FsdA$a2V5", t0.Add(time.Hour))
	if err != nil || id != u.ID {
		t.Fatalf("CompletePasswordSetup = %q, %v", id, err)
	}
	got, _ := s.GetUserByID(ctx, u.ID)
	if got.Status != StatusActive || got.PasswordHash == nil {
		t.Errorf("after setup: %+v", got)
	}
	if sess, _ := s.GetSession(ctx, hashOf("old")); sess != nil {
		t.Error("setup kept an existing session")
	}

	if _, err := s.CompletePasswordSetup(ctx, tok.TokenHash, "$argon2id$v=19$m=64,t=1,p=1$c2FsdA$eA",
		t0.Add(2*time.Hour)); !errors.Is(err, ErrTokenUnusable) {
		t.Errorf("second use: %v, want ErrTokenUnusable", err)
	}
	if usable, _ := s.GetUsableToken(ctx, tok.TokenHash, t0.Add(2*time.Hour)); usable != nil {
		t.Error("a used token is still usable")
	}
}

func TestOneTimeTokenExpiresAndANewOneReplacesIt(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	u := mustUser(t, s, "asha@hospital.test")
	first := OneTimeToken{TokenHash: hashOf("first"), UserID: u.ID, Purpose: PurposeSetup, CreatedAt: t0,
		ExpiresAt: t0.Add(time.Hour)}
	_ = s.CreateOneTimeToken(ctx, first)

	if got, _ := s.GetUsableToken(ctx, first.TokenHash, t0.Add(time.Hour)); got != nil {
		t.Error("a token is usable at its expiry time")
	}
	if _, err := s.CompletePasswordSetup(ctx, first.TokenHash, "x", t0.Add(2*time.Hour)); !errors.Is(err, ErrTokenUnusable) {
		t.Errorf("expired token: %v, want ErrTokenUnusable", err)
	}

	second := first
	second.TokenHash, second.ExpiresAt = hashOf("second"), t0.Add(72*time.Hour)
	_ = s.CreateOneTimeToken(ctx, OneTimeToken{TokenHash: hashOf("again"), UserID: u.ID, Purpose: PurposeSetup,
		CreatedAt: t0, ExpiresAt: t0.Add(72 * time.Hour)})
	_ = s.CreateOneTimeToken(ctx, second)
	if got, _ := s.GetUsableToken(ctx, hashOf("again"), t0); got != nil {
		t.Error("an older unused link still works after a new one was issued")
	}
	if got, _ := s.GetUsableToken(ctx, second.TokenHash, t0); got == nil {
		t.Error("the newest link doesn't work")
	}
}

func TestOneTimeTokenOfADisabledAccountIsUnusable(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	u := mustUser(t, s, "asha@hospital.test")
	activate(t, s, u.ID)
	tok := OneTimeToken{TokenHash: hashOf("reset"), UserID: u.ID, Purpose: PurposeReset, CreatedAt: t0,
		ExpiresAt: t0.Add(24 * time.Hour)}
	_ = s.CreateOneTimeToken(ctx, tok)
	_ = s.SetUserStatus(ctx, u.ID, StatusDisabled)

	if got, _ := s.GetUsableToken(ctx, tok.TokenHash, t0); got != nil {
		t.Error("a disabled account's link is usable")
	}
	if _, err := s.CompletePasswordSetup(ctx, tok.TokenHash, "x", t0); !errors.Is(err, ErrTokenUnusable) {
		t.Errorf("error = %v, want ErrTokenUnusable", err)
	}
	// The failed attempt left the token unused (the transaction rolled back).
	var used bool
	_ = s.pool.QueryRow(ctx, `SELECT used_at IS NOT NULL FROM one_time_tokens WHERE token_hash = $1`,
		tok.TokenHash).Scan(&used)
	if used {
		t.Error("a refused setup marked the token used")
	}

}
