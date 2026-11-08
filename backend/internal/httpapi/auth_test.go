package httpapi

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"backend/internal/auth"
	"backend/internal/db"
)

// ----- Login -----

func TestLoginStartsASession(t *testing.T) {
	e := newTestEnv(t)
	u := e.store.addUser("asha@hospital.test", db.StatusActive)

	req := httptest.NewRequest(http.MethodPost, "/auth/login",
		strings.NewReader(`{"email": "asha@hospital.test", "password": "`+testPassword+`"}`))
	req.Header.Set(csrfHeader, csrfValue)
	req.Header.Set("User-Agent", "test-browser")
	req.RemoteAddr = "10.1.2.3:5555"
	rec := httptest.NewRecorder()
	e.server.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", rec.Code, rec.Body)
	}
	me := decodeJSON[meResponse](t, rec)
	if me.ID != u.ID || me.Email != u.Email || me.Org.Name != "Test Hospital" || len(me.Roles) != 1 {
		t.Errorf("me = %+v", me)
	}

	c := sessionCookieFrom(t, rec)
	if c == nil {
		t.Fatal("no session cookie")
	}
	if !c.HttpOnly || !c.Secure || c.SameSite != http.SameSiteStrictMode || c.Path != "/" {
		t.Errorf("cookie = %+v, want HttpOnly, Secure, SameSite=Strict, Path=/", c)
	}
	if c.MaxAge != int((12 * time.Hour).Seconds()) {
		t.Errorf("cookie MaxAge = %d, want 12 hours", c.MaxAge)
	}

	if len(e.store.sessions) != 1 {
		t.Fatalf("%d sessions stored, want 1", len(e.store.sessions))
	}
	hash, _ := auth.HashToken(c.Value)
	sess, ok := e.store.sessions[string(hash)]
	if !ok {
		t.Fatal("the session is not stored under the hash of its cookie")
	}
	if _, raw := e.store.sessions[c.Value]; raw {
		t.Error("the cookie value itself is stored")
	}
	if !sess.ExpiresAt.Equal(e.now().Add(12*time.Hour)) || sess.IP != "10.1.2.3" || sess.UserAgent != "test-browser" {
		t.Errorf("session = %+v", sess)
	}
	if got := e.store.user(u.ID); got.LastLoginAt == nil || !got.LastLoginAt.Equal(e.now()) {
		t.Errorf("last_login_at = %v, want now", got.LastLoginAt)
	}
}

func TestLoginEmailIsCaseInsensitive(t *testing.T) {
	e := newTestEnv(t)
	e.store.addUser("asha@hospital.test", db.StatusActive)

	e.loginAs("  Asha@Hospital.TEST ", testPassword)
}

func TestEachLoginIsANewSession(t *testing.T) {
	e := newTestEnv(t)
	_, first := e.login("asha@hospital.test")

	second := e.loginAs("asha@hospital.test", testPassword)

	if first == second || len(e.store.sessions) != 2 {
		t.Errorf("a login reused a session (%d sessions)", len(e.store.sessions))
	}
}

func TestFailedLoginsLookTheSame(t *testing.T) {
	cases := map[string]struct {
		status   string // account to create; "" for none
		email    string
		password string
	}{
		"unknown email":    {"", "nobody@hospital.test", testPassword},
		"wrong password":   {db.StatusActive, "asha@hospital.test", "the wrong password"},
		"invited account":  {db.StatusInvited, "asha@hospital.test", testPassword},
		"disabled account": {db.StatusDisabled, "asha@hospital.test", testPassword},
		"not an email":     {db.StatusActive, "asha", testPassword},
		"huge password":    {db.StatusActive, "asha@hospital.test", strings.Repeat("x", 600)},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			e := newTestEnv(t)
			if tc.status != "" {
				e.store.addUser("asha@hospital.test", tc.status)
			}

			rec := e.postJSON("/auth/login", "", map[string]string{"email": tc.email, "password": tc.password})

			if rec.Code != http.StatusUnauthorized {
				t.Errorf("status = %d, want 401", rec.Code)
			}
			if got := strings.TrimSpace(rec.Body.String()); got != errLoginFailed {
				t.Errorf("message = %q, want the one login failure message", got)
			}
			if sessionCookieFrom(t, rec) != nil || len(e.store.sessions) != 0 {
				t.Error("a failed login started a session")
			}
		})
	}
}

func TestLoginRejectsBadRequests(t *testing.T) {
	tests := []struct {
		name, method, body string
		wantCode           int
	}{
		{"not JSON", http.MethodPost, "email=a", http.StatusBadRequest},
		{"no fields", http.MethodPost, `{}`, http.StatusBadRequest},
		{"no password", http.MethodPost, `{"email": "asha@hospital.test"}`, http.StatusBadRequest},
		{"body too large", http.MethodPost, `{"email": "` + strings.Repeat("a", 5000) + `"}`, http.StatusBadRequest},
		{"GET", http.MethodGet, "", http.StatusMethodNotAllowed},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newTestEnv(t)

			rec := e.do(tt.method, "/auth/login", "", strings.NewReader(tt.body), "application/json")

			if rec.Code != tt.wantCode {
				t.Errorf("status = %d, want %d", rec.Code, tt.wantCode)
			}
		})
	}
}

func TestFiveWrongPasswordsLockTheAccount(t *testing.T) {
	e := newTestEnv(t)
	u := e.store.addUser("asha@hospital.test", db.StatusActive)
	wrong := map[string]string{"email": "asha@hospital.test", "password": "the wrong password"}
	right := map[string]string{"email": "asha@hospital.test", "password": testPassword}

	for i := 0; i < 4; i++ {
		e.postJSON("/auth/login", "", wrong)
	}
	if e.store.user(u.ID).LockedUntil != nil {
		t.Fatal("locked after 4 failures, want 5")
	}
	e.postJSON("/auth/login", "", wrong)
	locked := e.store.user(u.ID).LockedUntil
	if locked == nil || !locked.Equal(e.now().Add(15*time.Minute)) {
		t.Fatalf("locked_until = %v, want 15 minutes from now", locked)
	}

	// While locked, even the right password is refused, with the same answer.
	e.advance(14 * time.Minute)
	if rec := e.postJSON("/auth/login", "", right); rec.Code != http.StatusUnauthorized {
		t.Fatalf("a locked account logged in: %d", rec.Code)
	}
	// Attempts during the lock are not checked, so they don't extend it.
	if got := e.store.user(u.ID); !got.LockedUntil.Equal(*locked) || got.FailedLogins != 0 {
		t.Errorf("an attempt during the lock changed it: %+v", got)
	}

	e.advance(time.Minute)
	e.loginAs("asha@hospital.test", testPassword)
	if got := e.store.user(u.ID); got.LockedUntil != nil || got.FailedLogins != 0 {
		t.Errorf("a successful login left lock state: %+v", got)
	}
}

func TestASuccessfulLoginResetsTheFailureCount(t *testing.T) {
	e := newTestEnv(t)
	u := e.store.addUser("asha@hospital.test", db.StatusActive)
	for i := 0; i < 3; i++ {
		e.postJSON("/auth/login", "", map[string]string{"email": "asha@hospital.test", "password": "wrong password!"})
	}

	e.loginAs("asha@hospital.test", testPassword)

	if got := e.store.user(u.ID).FailedLogins; got != 0 {
		t.Errorf("failed_logins = %d after a success, want 0", got)
	}
}

func TestLoginRehashesAnOldHash(t *testing.T) {
	e := newTestEnv(t)
	u := e.store.addUser("asha@hospital.test", db.StatusActive)
	older, err := auth.NewHasher(auth.Params{Memory: 32, Time: 1, Threads: 1, SaltLen: 16, KeyLen: 32})
	if err != nil {
		t.Fatal(err)
	}
	oldHash, _ := older.Hash(testPassword)
	_ = e.store.SetPasswordHash(context.Background(), u.ID, oldHash, e.now())

	e.loginAs("asha@hospital.test", testPassword)

	newHash := *e.store.user(u.ID).PasswordHash
	if newHash == oldHash || testHasher.NeedsRehash(newHash) {
		t.Errorf("the hash was not upgraded to the current parameters: %s", newHash)
	}
	e.loginAs("asha@hospital.test", testPassword) // and the new hash works
}

// ----- Sessions -----

func TestMeReturnsTheLoggedInAccount(t *testing.T) {
	e := newTestEnv(t)
	id, session := e.login("asha@hospital.test")

	rec := e.do(http.MethodGet, "/auth/me", session, nil, "")

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if me := decodeJSON[meResponse](t, rec); me.ID != id || me.Email != "asha@hospital.test" || me.Org.ID != "org-1" {
		t.Errorf("me = %+v", me)
	}
}

func TestSessionEndsAfterThirtyIdleMinutes(t *testing.T) {
	e := newTestEnv(t)
	_, session := e.login("asha@hospital.test")
	me := func() int { return e.do(http.MethodGet, "/auth/me", session, nil, "").Code }

	// Activity keeps it alive: the idle timeout slides.
	for i := 0; i < 3; i++ {
		e.advance(29 * time.Minute)
		if code := me(); code != http.StatusOK {
			t.Fatalf("after %d idle periods of 29 minutes: %d, want 200", i+1, code)
		}
	}

	e.advance(30 * time.Minute)
	rec := e.do(http.MethodGet, "/auth/me", session, nil, "")
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("after 30 idle minutes: %d, want 401", rec.Code)
	}
	if c := sessionCookieFrom(t, rec); c == nil || c.MaxAge >= 0 {
		t.Error("the expired session's cookie was not cleared")
	}
	if len(e.store.sessions) != 0 {
		t.Error("the expired session was not removed")
	}
}

func TestSessionEndsTwelveHoursAfterLogin(t *testing.T) {
	e := newTestEnv(t)
	_, session := e.login("asha@hospital.test")

	for elapsed := 20 * time.Minute; elapsed < 12*time.Hour; elapsed += 20 * time.Minute {
		e.advance(20 * time.Minute)
		if code := e.do(http.MethodGet, "/auth/me", session, nil, "").Code; code != http.StatusOK {
			t.Fatalf("after %v of activity: %d, want 200", elapsed, code)
		}
	}
	e.advance(20 * time.Minute)
	if code := e.do(http.MethodGet, "/auth/me", session, nil, "").Code; code != http.StatusUnauthorized {
		t.Errorf("12 hours after login: %d, want 401", code)
	}
}

func TestSessionActivityIsWrittenAtMostOnceAMinute(t *testing.T) {
	e := newTestEnv(t)
	_, session := e.login("asha@hospital.test")
	hash, _ := auth.HashToken(session)
	start := e.now()

	e.advance(30 * time.Second)
	e.do(http.MethodGet, "/auth/me", session, nil, "")
	if got := e.store.sessions[string(hash)].LastSeenAt; !got.Equal(start) {
		t.Errorf("last_seen_at written after 30 s: %v", got)
	}
	e.advance(30 * time.Second)
	e.do(http.MethodGet, "/auth/me", session, nil, "")
	if got := e.store.sessions[string(hash)].LastSeenAt; !got.Equal(e.now()) {
		t.Errorf("last_seen_at = %v after a minute, want now", got)
	}
}

func TestADisabledAccountLosesItsSession(t *testing.T) {
	e := newTestEnv(t)
	id, session := e.login("asha@hospital.test")
	e.store.users[id].Status = db.StatusDisabled

	if code := e.do(http.MethodGet, "/documents", session, nil, "").Code; code != http.StatusUnauthorized {
		t.Errorf("a disabled account's session was accepted: %d", code)
	}
	if e.store.sessionsOf(id) != 0 {
		t.Error("the disabled account's session was not removed")
	}
}

func TestUnknownOrMalformedSessionCookies(t *testing.T) {
	e := newTestEnv(t)
	unknown, _, _ := auth.NewToken()
	for name, value := range map[string]string{"unknown": unknown, "malformed": "not-a-token", "empty": ""} {
		t.Run(name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/auth/me", nil)
			req.AddCookie(&http.Cookie{Name: sessionCookie, Value: value})
			rec := httptest.NewRecorder()
			e.server.ServeHTTP(rec, req)
			if rec.Code != http.StatusUnauthorized {
				t.Errorf("status = %d, want 401", rec.Code)
			}
		})
	}
}

func TestPurgeExpiredSessions(t *testing.T) {
	e := newTestEnv(t)
	e.login("asha@hospital.test")
	e.advance(10 * time.Minute)
	e.login("ravi@hospital.test")

	e.advance(25 * time.Minute) // the first is 35 minutes idle, the second 25
	e.server.PurgeExpiredSessions(context.Background())

	if len(e.store.sessions) != 1 {
		t.Errorf("%d sessions left, want 1", len(e.store.sessions))
	}
}

// ----- Logout -----

func TestLogoutEndsTheSession(t *testing.T) {
	e := newTestEnv(t)
	id, session := e.login("asha@hospital.test")
	_, other := e.login("ravi@hospital.test")

	rec := e.do(http.MethodPost, "/auth/logout", session, nil, "")

	if rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204", rec.Code)
	}
	if c := sessionCookieFrom(t, rec); c == nil || c.MaxAge >= 0 {
		t.Error("logout did not clear the cookie")
	}
	if e.store.sessionsOf(id) != 0 {
		t.Error("the session is still stored")
	}
	if code := e.do(http.MethodGet, "/auth/me", session, nil, "").Code; code != http.StatusUnauthorized {
		t.Errorf("the session works after logout: %d", code)
	}
	if code := e.do(http.MethodGet, "/auth/me", other, nil, "").Code; code != http.StatusOK {
		t.Errorf("another account's session ended too: %d", code)
	}
}

func TestLogoutWithoutASessionSucceeds(t *testing.T) {
	e := newTestEnv(t)
	if code := e.do(http.MethodPost, "/auth/logout", "", nil, "").Code; code != http.StatusNoContent {
		t.Errorf("status = %d, want 204", code)
	}
}

// ----- Change password -----

func changePassword(e *testEnv, session, current, next string) *httptest.ResponseRecorder {
	return e.postJSON("/auth/password", session, map[string]string{"current_password": current, "new_password": next})
}

func TestChangePassword(t *testing.T) {
	e := newTestEnv(t)
	id, session := e.login("asha@hospital.test")
	otherDevice := e.loginAs("asha@hospital.test", testPassword)

	rec := changePassword(e, session, testPassword, "a brand new passphrase")

	if rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if code := e.do(http.MethodGet, "/auth/me", session, nil, "").Code; code != http.StatusOK {
		t.Errorf("the session that changed the password ended: %d", code)
	}
	if code := e.do(http.MethodGet, "/auth/me", otherDevice, nil, "").Code; code != http.StatusUnauthorized {
		t.Errorf("another session of the account survived a password change: %d", code)
	}
	if e.store.sessionsOf(id) != 1 {
		t.Errorf("%d sessions, want 1", e.store.sessionsOf(id))
	}
	if code := e.postJSON("/auth/login", "", map[string]string{"email": "asha@hospital.test", "password": testPassword}).Code; code != http.StatusUnauthorized {
		t.Error("the old password still works")
	}
	e.loginAs("asha@hospital.test", "a brand new passphrase")
}

func TestChangePasswordRefusals(t *testing.T) {
	tests := []struct {
		name, current, next string
		wantCode            int
	}{
		{"wrong current password", "not my password", "a brand new passphrase", http.StatusForbidden},
		{"same as before", testPassword, testPassword, http.StatusBadRequest},
		{"too short", testPassword, "short", http.StatusBadRequest},
		{"common", testPassword, "Password1234", http.StatusBadRequest},
		{"contains the email name", testPassword, "asha is a doctor here", http.StatusBadRequest},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newTestEnv(t)
			id, session := e.login("asha@hospital.test")
			before := *e.store.user(id).PasswordHash

			if rec := changePassword(e, session, tt.current, tt.next); rec.Code != tt.wantCode {
				t.Errorf("status = %d, want %d: %s", rec.Code, tt.wantCode, rec.Body)
			}
			if *e.store.user(id).PasswordHash != before {
				t.Error("the password changed")
			}
		})
	}
}

func TestAWrongCurrentPasswordCountsTowardsTheLock(t *testing.T) {
	e := newTestEnv(t)
	id, session := e.login("asha@hospital.test")

	changePassword(e, session, "not my password", "a brand new passphrase")

	if got := e.store.user(id).FailedLogins; got != 1 {
		t.Errorf("failed_logins = %d, want 1", got)
	}
}

// ----- Setup and reset links -----

func TestSetupLinkActivatesAnInvitedAccount(t *testing.T) {
	e := newTestEnv(t)
	u := e.store.addUser("asha@hospital.test", db.StatusInvited)
	token := e.store.addToken(u.ID, db.PurposeSetup, e.now().Add(72*time.Hour))

	info := e.postJSON("/auth/setup/info", "", map[string]string{"token": token})
	if info.Code != http.StatusOK {
		t.Fatalf("setup/info status = %d: %s", info.Code, info.Body)
	}
	if got := decodeJSON[setupInfoResponse](t, info); got.Email != u.Email || got.Org != "Test Hospital" {
		t.Errorf("setup/info = %+v", got)
	}

	rec := e.postJSON("/auth/setup", "", map[string]string{"token": token, "password": "my own passphrase"})
	if rec.Code != http.StatusNoContent {
		t.Fatalf("setup status = %d: %s", rec.Code, rec.Body)
	}
	if sessionCookieFrom(t, rec) != nil {
		t.Error("setup logged the user in; they should log in with the new password")
	}
	if got := e.store.user(u.ID); got.Status != db.StatusActive {
		t.Errorf("status = %s, want active", got.Status)
	}
	e.loginAs("asha@hospital.test", "my own passphrase")

	// Single use.
	again := e.postJSON("/auth/setup", "", map[string]string{"token": token, "password": "another passphrase"})
	if again.Code != http.StatusBadRequest {
		t.Errorf("a used link worked again: %d", again.Code)
	}
}

func TestResetLinkEndsTheAccountsSessions(t *testing.T) {
	e := newTestEnv(t)
	id, session := e.login("asha@hospital.test")
	token := e.store.addToken(id, db.PurposeReset, e.now().Add(24*time.Hour))

	if rec := e.postJSON("/auth/setup", "", map[string]string{"token": token, "password": "a fresh passphrase"}); rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if code := e.do(http.MethodGet, "/auth/me", session, nil, "").Code; code != http.StatusUnauthorized {
		t.Errorf("a session survived a password reset: %d", code)
	}
}

func TestSetupLinkRefusals(t *testing.T) {
	tests := []struct {
		name  string
		setup func(e *testEnv) string // returns the token to use
	}{
		{"malformed", func(e *testEnv) string { return "not-a-token" }},
		{"unknown", func(e *testEnv) string { token, _, _ := auth.NewToken(); return token }},
		{"expired", func(e *testEnv) string {
			u := e.store.addUser("asha@hospital.test", db.StatusInvited)
			return e.store.addToken(u.ID, db.PurposeSetup, e.now())
		}},
		{"disabled account", func(e *testEnv) string {
			u := e.store.addUser("asha@hospital.test", db.StatusDisabled)
			return e.store.addToken(u.ID, db.PurposeReset, e.now().Add(time.Hour))
		}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newTestEnv(t)
			token := tt.setup(e)

			for _, path := range []string{"/auth/setup/info", "/auth/setup"} {
				rec := e.postJSON(path, "", map[string]string{"token": token, "password": "my own passphrase"})
				if rec.Code != http.StatusBadRequest {
					t.Errorf("%s status = %d, want 400", path, rec.Code)
				}
				if got := strings.TrimSpace(rec.Body.String()); got != db.ErrTokenUnusable.Error() {
					t.Errorf("%s message = %q", path, got)
				}
			}
		})
	}
}

func TestSetupEnforcesThePasswordRulesAndKeepsTheLink(t *testing.T) {
	e := newTestEnv(t)
	u := e.store.addUser("asha@hospital.test", db.StatusInvited)
	token := e.store.addToken(u.ID, db.PurposeSetup, e.now().Add(time.Hour))

	rec := e.postJSON("/auth/setup", "", map[string]string{"token": token, "password": "short"})
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "at least 12") {
		t.Fatalf("status = %d %q, want 400 with the rule", rec.Code, rec.Body)
	}
	if e.store.user(u.ID).Status != db.StatusInvited {
		t.Error("a refused password activated the account")
	}
	if rec := e.postJSON("/auth/setup", "", map[string]string{"token": token, "password": "a good passphrase"}); rec.Code != http.StatusNoContent {
		t.Errorf("the link no longer works after a refused password: %d", rec.Code)
	}
}

// ----- CSRF and CORS -----

func TestStateChangingRequestsNeedTheCSRFHeader(t *testing.T) {
	e := newTestEnv(t)
	_, session := e.login("asha@hospital.test")
	for _, r := range []struct{ method, path string }{
		{http.MethodPost, "/auth/login"},
		{http.MethodPost, "/auth/logout"},
		{http.MethodPost, "/auth/setup"},
		{http.MethodPost, "/documents"},
		{http.MethodDelete, "/documents/" + unknownID},
		{http.MethodPost, "/user/insights"},
	} {
		req := httptest.NewRequest(r.method, r.path, strings.NewReader("{}"))
		req.AddCookie(&http.Cookie{Name: sessionCookie, Value: session})
		rec := httptest.NewRecorder()
		e.server.ServeHTTP(rec, req)
		if rec.Code != http.StatusForbidden {
			t.Errorf("%s %s without %s = %d, want 403", r.method, r.path, csrfHeader, rec.Code)
		}
	}
	if len(e.ai.received()) != 0 {
		t.Error("a request without the header reached the AI service")
	}
	if code := e.do(http.MethodGet, "/auth/me", session, nil, "").Code; code != http.StatusOK {
		t.Errorf("logout ran without the header: /auth/me = %d", code)
	}
}

func TestRequestsFromOtherOriginsAreRefused(t *testing.T) {
	e := newTestEnv(t)
	_, session := e.login("asha@hospital.test")
	send := func(origin string) int {
		req := httptest.NewRequest(http.MethodPost, "/user/insights", nil)
		req.AddCookie(&http.Cookie{Name: sessionCookie, Value: session})
		req.Header.Set(csrfHeader, csrfValue)
		if origin != "" {
			req.Header.Set("Origin", origin)
		}
		rec := httptest.NewRecorder()
		e.server.ServeHTTP(rec, req)
		return rec.Code
	}

	if code := send("https://evil.example"); code != http.StatusForbidden {
		t.Errorf("another origin = %d, want 403", code)
	}
	if code := send("http://localhost:5174"); code != http.StatusForbidden {
		t.Errorf("another port = %d, want 403", code)
	}
	if code := send(testOrigin); code != http.StatusAccepted {
		t.Errorf("the allowed origin = %d, want 202", code)
	}
	if code := send(""); code != http.StatusAccepted {
		t.Errorf("no origin (not a browser) = %d, want 202", code)
	}
}

func TestCORSPreflight(t *testing.T) {
	e := newTestEnv(t)
	preflight := func(origin string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodOptions, "/auth/login", nil)
		req.Header.Set("Origin", origin)
		req.Header.Set("Access-Control-Request-Method", "POST")
		rec := httptest.NewRecorder()
		e.server.ServeHTTP(rec, req)
		return rec
	}

	ok := preflight(testOrigin)
	h := ok.Header()
	if ok.Code != http.StatusNoContent || h.Get("Access-Control-Allow-Origin") != testOrigin ||
		h.Get("Access-Control-Allow-Credentials") != "true" ||
		!strings.Contains(h.Get("Access-Control-Allow-Headers"), csrfHeader) {
		t.Errorf("allowed origin preflight = %d %v", ok.Code, h)
	}
	if h.Get("Vary") != "Origin" {
		t.Errorf("Vary = %q, want Origin", h.Get("Vary"))
	}

	bad := preflight("https://evil.example")
	if bad.Header().Get("Access-Control-Allow-Origin") != "" || bad.Header().Get("Access-Control-Allow-Credentials") != "" {
		t.Errorf("another origin got CORS headers: %v", bad.Header())
	}
}

func TestParseOrigins(t *testing.T) {
	got := ParseOrigins(" http://localhost:5173/ , https://med-sum.example,,")
	want := []string{"http://localhost:5173", "https://med-sum.example"}
	if strings.Join(got, " ") != strings.Join(want, " ") {
		t.Errorf("ParseOrigins = %v, want %v", got, want)
	}
}

// postRaw is used where a body must be sent exactly.
func postRaw(e *testEnv, path, body string) *httptest.ResponseRecorder {
	return e.do(http.MethodPost, path, "", bytes.NewBufferString(body), "application/json")
}

func TestSetupRejectsBadRequests(t *testing.T) {
	e := newTestEnv(t)
	for _, path := range []string{"/auth/setup", "/auth/setup/info", "/auth/password"} {
		if code := postRaw(e, path, "not json").Code; code != http.StatusBadRequest && code != http.StatusUnauthorized {
			t.Errorf("%s with a bad body = %d", path, code)
		}
		if code := e.do(http.MethodGet, path, "", nil, "").Code; code != http.StatusMethodNotAllowed && code != http.StatusUnauthorized {
			t.Errorf("GET %s = %d", path, code)
		}
	}
}
