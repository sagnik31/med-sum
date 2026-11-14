package httpapi

import (
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"backend/internal/db"
)

func loginFrom(e *testEnv, addr, password string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/auth/login",
		strings.NewReader(`{"email":"me@hospital.test","password":"`+password+`"}`))
	req.RemoteAddr = addr
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(csrfHeader, csrfValue)
	rec := httptest.NewRecorder()
	e.server.ServeHTTP(rec, req)
	return rec
}

func TestAnAddressGetsTenLoginAttemptsAMinute(t *testing.T) {
	e := newTestEnv(t)
	e.store.addUser("me@hospital.test", db.StatusActive)
	for i := range authAttemptsPerWindow {
		if rec := loginFrom(e, "198.51.100.7:1234", "wrong password, long enough"); rec.Code != http.StatusUnauthorized {
			t.Fatalf("attempt %d: status %d", i+1, rec.Code)
		}
	}

	rec := loginFrom(e, "198.51.100.7:5678", testPassword) // the right password, still refused
	if rec.Code != http.StatusTooManyRequests || rec.Header().Get("Retry-After") == "" {
		t.Errorf("status %d, Retry-After %q", rec.Code, rec.Header().Get("Retry-After"))
	}
	if got := e.store.auditActions(); got[len(got)-1] != actRateLimited {
		t.Errorf("the refusal wasn't recorded: %v", got)
	}
	// Another address isn't held back.
	if rec := loginFrom(e, "203.0.113.9:1", "wrong password, long enough"); rec.Code == http.StatusTooManyRequests {
		t.Error("another address was limited")
	}
}

func TestTheLimitClearsAfterAMinute(t *testing.T) {
	e := newTestEnv(t)
	e.store.addUser("me@hospital.test", db.StatusActive)
	for range authAttemptsPerWindow {
		loginFrom(e, "198.51.100.7:1", testPassword)
	}
	if rec := loginFrom(e, "198.51.100.7:1", testPassword); rec.Code != http.StatusTooManyRequests {
		t.Fatalf("status %d, want 429", rec.Code)
	}
	e.advance(authWindow + time.Second)
	if rec := loginFrom(e, "198.51.100.7:1", testPassword); rec.Code != http.StatusOK {
		t.Errorf("status %d after the window", rec.Code)
	}
}

func TestOnlyTheRoutesThatCheckASecretAreLimited(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	for range 3 * authAttemptsPerWindow {
		if rec := e.do(http.MethodGet, "/auth/me", auth, nil, ""); rec.Code != http.StatusOK {
			t.Fatalf("GET /auth/me: status %d", rec.Code)
		}
	}
	for _, path := range []string{"/auth/password", "/auth/setup", "/auth/setup/info"} {
		e := newTestEnv(t)
		for range authAttemptsPerWindow {
			e.postJSON(path, "", map[string]string{})
		}
		if rec := e.postJSON(path, "", map[string]string{}); rec.Code != http.StatusTooManyRequests {
			t.Errorf("%s: status %d, want 429", path, rec.Code)
		}
	}
}

func TestTheLimiterForgetsIdleAddressesWhenFull(t *testing.T) {
	l := newRateLimiter(2, time.Minute)
	start := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	for i := range rateLimiterMaxKeys {
		l.allow(strings.Repeat("a", i%7)+string(rune('A'+i%26))+time.Duration(i).String(), start)
	}
	l.allow("new", start.Add(2*time.Minute))
	if n := len(l.attempts); n != 1 {
		t.Errorf("%d addresses kept, want only the new one", n)
	}
}

func TestEveryResponseCarriesTheSecurityHeaders(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	for name, rec := range map[string]*httptest.ResponseRecorder{
		"health":          e.do(http.MethodGet, "/healthz", "", nil, ""),
		"not logged in":   e.do(http.MethodGet, "/patients", "", nil, ""),
		"json":            e.do(http.MethodGet, "/patients", auth, nil, ""),
		"not found":       e.do(http.MethodGet, "/nowhere", auth, nil, ""),
		"no csrf header":  e.do(http.MethodPost, "/auth/logout", "", nil, ""),
		"preflight":       e.do(http.MethodOptions, "/patients", "", nil, ""),
		"too many logins": tooMany(e),
	} {
		h := rec.Header()
		if h.Get("Content-Security-Policy") != "default-src 'none'; frame-ancestors 'none'" ||
			h.Get("X-Content-Type-Options") != "nosniff" || h.Get("X-Frame-Options") != "DENY" ||
			h.Get("Referrer-Policy") != "no-referrer" || h.Get("Cache-Control") != "no-store" ||
			h.Get("Strict-Transport-Security") == "" {
			t.Errorf("%s (%d): headers %v", name, rec.Code, h)
		}
	}
}

func tooMany(e *testEnv) *httptest.ResponseRecorder {
	var rec *httptest.ResponseRecorder
	for range authAttemptsPerWindow + 1 {
		rec = loginFrom(e, "192.0.2.200:1", "x")
	}
	return rec
}

func TestNoHSTSOverPlainHTTP(t *testing.T) {
	e := newTestEnv(t)
	e.server.cookieSecure = false
	if h := e.do(http.MethodGet, "/healthz", "", nil, "").Header().Get("Strict-Transport-Security"); h != "" {
		t.Errorf("HSTS = %q with COOKIE_SECURE=false", h)
	}
}

func TestResponsesSayWhenTheSessionEnds(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	login := e.now()
	secondsLeft := func() string {
		return e.do(http.MethodGet, "/auth/me", auth, nil, "").Header().Get(sessionExpiresHeader)
	}
	seconds := func(d time.Duration) string { return strconv.Itoa(int(d / time.Second)) }

	if got := secondsLeft(); got != seconds(idleTimeout) {
		t.Errorf("right after login: %q, want %q", got, seconds(idleTimeout))
	}
	// A request within touchEvery doesn't move last_seen_at, so the time
	// left goes down.
	e.advance(touchEvery / 2)
	if got := secondsLeft(); got != seconds(idleTimeout-touchEvery/2) {
		t.Errorf("within a minute: %q", got)
	}
	e.advance(10 * time.Minute)
	if got := secondsLeft(); got != seconds(idleTimeout) {
		t.Errorf("after a touch: %q", got)
	}
	// Near the end of the shift, the absolute timeout comes first.
	for e.now().Sub(login) < absoluteTimeout-10*time.Minute {
		e.advance(20 * time.Minute)
		secondsLeft()
	}
	if got, want := secondsLeft(), seconds(login.Add(absoluteTimeout).Sub(e.now())); got != want {
		t.Errorf("near the absolute end: %q, want %q", got, want)
	}
	if h := e.do(http.MethodGet, "/healthz", "", nil, "").Header().Get(sessionExpiresHeader); h != "" {
		t.Errorf("a public route says %q", h)
	}
}

func TestBrowsersMayReadTheSessionEnd(t *testing.T) {
	e := newTestEnv(t)
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	req.Header.Set("Origin", testOrigin)
	rec := httptest.NewRecorder()
	e.server.ServeHTTP(rec, req)
	if got := rec.Header().Get("Access-Control-Expose-Headers"); got != sessionExpiresHeader {
		t.Errorf("exposed headers = %q", got)
	}
}
