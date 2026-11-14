package httpapi

import (
	"log"
	"net/http"
	"strconv"
	"sync"
	"time"

	"backend/internal/db"
)

// ----- Security headers -----

// withSecurityHeaders sets headers on every response. The API serves JSON
// and the patient pages as data, never pages of its own, so nothing it
// returns may run script, be framed, be sniffed as another type or be
// cached: responses carry patient data.
// See docs/adr/0010-staff-access-and-security.md.
func (s *Server) withSecurityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("Cache-Control", "no-store")
		if s.cookieSecure {
			h.Set("Strict-Transport-Security", "max-age=31536000")
		}
		next.ServeHTTP(w, r)
	})
}

// ----- Login attempts per address -----

// Each address may make authAttemptsPerWindow attempts on the routes that
// check a password or a link token within authWindow; more answer 429.
// This slows guessing across accounts, which the per-account lockout
// doesn't. It is in memory: one API process, and a restart forgets it.
const (
	authAttemptsPerWindow = 10
	authWindow            = time.Minute
	// rateLimiterMaxKeys bounds the memory a flood of addresses can use;
	// past it, addresses with no recent attempt are forgotten.
	rateLimiterMaxKeys = 10000
)

// limitedAuthPaths are the routes that check a secret.
var limitedAuthPaths = map[string]bool{
	"/auth/login":      true,
	"/auth/password":   true,
	"/auth/setup":      true,
	"/auth/setup/info": true,
}

type rateLimiter struct {
	mu       sync.Mutex
	limit    int
	window   time.Duration
	attempts map[string][]time.Time // address -> attempt times in the window, oldest first
}

func newRateLimiter(limit int, window time.Duration) *rateLimiter {
	return &rateLimiter{limit: limit, window: window, attempts: map[string][]time.Time{}}
}

// allow records an attempt from key at now and reports whether it is
// within the limit, and if not, how long until the oldest attempt expires.
func (l *rateLimiter) allow(key string, now time.Time) (bool, time.Duration) {
	l.mu.Lock()
	defer l.mu.Unlock()
	cutoff := now.Add(-l.window)
	recent := l.attempts[key]
	i := 0
	for i < len(recent) && !recent[i].After(cutoff) {
		i++
	}
	recent = recent[i:]
	if len(recent) >= l.limit {
		l.attempts[key] = recent
		return false, recent[0].Add(l.window).Sub(now)
	}
	if _, known := l.attempts[key]; !known && len(l.attempts) >= rateLimiterMaxKeys {
		l.forgetIdle(cutoff)
	}
	l.attempts[key] = append(recent, now)
	return true, 0
}

// forgetIdle drops addresses with no attempt after cutoff; call it holding l.mu.
func (l *rateLimiter) forgetIdle(cutoff time.Time) {
	for k, times := range l.attempts {
		if len(times) == 0 || !times[len(times)-1].After(cutoff) {
			delete(l.attempts, k)
		}
	}
}

// withAuthRateLimit answers 429 to an address past its attempts on the
// routes that check a secret, and records the refusal.
func (s *Server) withAuthRateLimit(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || !limitedAuthPaths[r.URL.Path] {
			next.ServeHTTP(w, r)
			return
		}
		ok, wait := s.authLimiter.allow(clientIP(r), s.now())
		if ok {
			next.ServeHTTP(w, r)
			return
		}
		ev := with(s.auditEvent(r, actRateLimited), map[string]any{"path": r.URL.Path})
		ev.Outcome = db.OutcomeDenied
		if err := s.db.RecordAudit(r.Context(), *ev); err != nil {
			log.Printf("RecordAudit error (action %s): %v", ev.Action, err) // refused either way
		}
		w.Header().Set("Retry-After", strconv.Itoa(int(wait.Seconds())+1))
		http.Error(w, "too many attempts; wait a minute and try again", http.StatusTooManyRequests)
	})
}

// ----- Session expiry, for the idle warning -----

// sessionExpiresHeader tells the frontend how many seconds are left
// before the session ends if no further request comes: the idle timeout
// from the last recorded activity, or the absolute timeout, whichever is
// first. Seconds rather than a time, so the browser's clock doesn't
// matter. The frontend warns before then and offers to stay signed in.
const sessionExpiresHeader = "X-Session-Expires-In"

func setSessionExpires(w http.ResponseWriter, sess db.Session, lastSeen, now time.Time) {
	end := lastSeen.Add(idleTimeout)
	if sess.ExpiresAt.Before(end) {
		end = sess.ExpiresAt
	}
	left := int(end.Sub(now) / time.Second)
	w.Header().Set(sessionExpiresHeader, strconv.Itoa(max(left, 0)))
}
