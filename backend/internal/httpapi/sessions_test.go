package httpapi

import (
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestListSessionsShowsMineAndMarksTheCurrentOne(t *testing.T) {
	e := newTestEnv(t)
	_, laptop := e.login("doc@hospital.test")
	e.advance(2 * time.Minute)
	phone := e.loginAs("doc@hospital.test", testPassword)
	e.login("other@hospital.test")

	rec := e.do(http.MethodGet, "/auth/sessions", phone, nil, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if strings.Contains(rec.Body.String(), "token") {
		t.Error("the response mentions tokens")
	}
	got := decodeJSON[[]sessionResponse](t, rec)
	if len(got) != 2 {
		t.Fatalf("got %d sessions, want 2: %+v", len(got), got)
	}
	if !got[0].Current || got[1].Current || got[0].ID == got[1].ID || got[0].ID == "" {
		t.Errorf("sessions = %+v, want the current (newest) one first and marked", got)
	}

	// An idle session is left out, though it waits for the purge.
	e.advance(29 * time.Minute)
	e.do(http.MethodGet, "/auth/me", phone, nil, "") // keeps the phone alive
	e.advance(2 * time.Minute)
	got = decodeJSON[[]sessionResponse](t, e.do(http.MethodGet, "/auth/sessions", phone, nil, ""))
	if len(got) != 1 || !got[0].Current {
		t.Errorf("sessions = %+v, want only the current one", got)
	}
	if rec := e.do(http.MethodGet, "/auth/me", laptop, nil, ""); rec.Code != http.StatusUnauthorized {
		t.Errorf("idle session: status = %d", rec.Code)
	}
}

func TestEndAllOtherSessions(t *testing.T) {
	e := newTestEnv(t)
	docID, laptop := e.login("doc@hospital.test")
	phone := e.loginAs("doc@hospital.test", testPassword)
	otherID, other := e.login("other@hospital.test")

	if rec := e.do(http.MethodDelete, "/auth/sessions", phone, nil, ""); rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if rec := e.do(http.MethodGet, "/auth/me", laptop, nil, ""); rec.Code != http.StatusUnauthorized {
		t.Errorf("the other session still works: %d", rec.Code)
	}
	if rec := e.do(http.MethodGet, "/auth/me", phone, nil, ""); rec.Code != http.StatusOK {
		t.Errorf("the current session ended: %d", rec.Code)
	}
	if e.store.sessionsOf(docID) != 1 || e.store.sessionsOf(otherID) != 1 {
		t.Error("wrong sessions removed")
	}
	if rec := e.do(http.MethodGet, "/auth/me", other, nil, ""); rec.Code != http.StatusOK {
		t.Errorf("someone else's session ended: %d", rec.Code)
	}
}

func TestEndOneSession(t *testing.T) {
	e := newTestEnv(t)
	_, laptop := e.login("doc@hospital.test")
	phone := e.loginAs("doc@hospital.test", testPassword)
	_, other := e.login("other@hospital.test")

	idOf := func(session string, current bool) string {
		for _, s := range decodeJSON[[]sessionResponse](t, e.do(http.MethodGet, "/auth/sessions", session, nil, "")) {
			if s.Current == current {
				return s.ID
			}
		}
		t.Fatal("session not found")
		return ""
	}
	laptopID := idOf(laptop, true)
	othersID := idOf(other, true)

	// Someone else's session, an unknown id and a malformed one are 404.
	for _, id := range []string{othersID, "00000000-0000-4000-8000-999999999999", "nope", ""} {
		if rec := e.do(http.MethodDelete, "/auth/sessions/"+id, phone, nil, ""); rec.Code != http.StatusNotFound {
			t.Errorf("DELETE %q: status = %d, want 404", id, rec.Code)
		}
	}
	if rec := e.do(http.MethodGet, "/auth/me", other, nil, ""); rec.Code != http.StatusOK {
		t.Errorf("someone else's session ended: %d", rec.Code)
	}

	rec := e.do(http.MethodDelete, "/auth/sessions/"+laptopID, phone, nil, "")
	if rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if c := sessionCookieFrom(t, rec); c != nil {
		t.Error("ending another session cleared this one's cookie")
	}
	if rec := e.do(http.MethodGet, "/auth/me", laptop, nil, ""); rec.Code != http.StatusUnauthorized {
		t.Errorf("the ended session still works: %d", rec.Code)
	}

	// Ending the current session logs out.
	rec = e.do(http.MethodDelete, "/auth/sessions/"+idOf(phone, true), phone, nil, "")
	if rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d", rec.Code)
	}
	if c := sessionCookieFrom(t, rec); c == nil || c.MaxAge >= 0 {
		t.Errorf("cookie = %+v, want it cleared", c)
	}
	if rec := e.do(http.MethodGet, "/auth/me", phone, nil, ""); rec.Code != http.StatusUnauthorized {
		t.Errorf("the current session still works: %d", rec.Code)
	}
}

func TestSessionRoutesNeedASession(t *testing.T) {
	e := newTestEnv(t)
	for _, tc := range []struct{ method, path string }{
		{http.MethodGet, "/auth/sessions"},
		{http.MethodDelete, "/auth/sessions"},
		{http.MethodDelete, "/auth/sessions/00000000-0000-4000-8000-000000000001"},
		{http.MethodGet, "/admin/users"},
		{http.MethodPatch, "/admin/users/00000000-0000-4000-8000-000000000001"},
	} {
		if rec := e.do(tc.method, tc.path, "", nil, ""); rec.Code != http.StatusUnauthorized {
			t.Errorf("%s %s: status = %d, want 401", tc.method, tc.path, rec.Code)
		}
	}
}
