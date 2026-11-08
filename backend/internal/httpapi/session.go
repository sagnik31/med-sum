package httpapi

import (
	"context"
	"log"
	"net"
	"net/http"
	"time"

	"backend/internal/auth"
	"backend/internal/db"
)

// Session rules.
const (
	sessionCookie   = "medsum_session"
	idleTimeout     = 30 * time.Minute // without a request
	absoluteTimeout = 12 * time.Hour   // from login: one shift
	touchEvery      = time.Minute      // last_seen_at is written at most this often
)

// Principal is the logged-in account behind a request.
type Principal struct {
	UserID   string
	OrgID    string
	OrgName  string
	Email    string
	FullName string
	Roles    []string

	tokenHash []byte // the current session
}

type principalKey struct{}

func principalFromContext(ctx context.Context) (*Principal, bool) {
	p, ok := ctx.Value(principalKey{}).(*Principal)
	return p, ok && p != nil
}

// userIDFromContext returns the logged-in account's ID.
func userIDFromContext(ctx context.Context) (string, bool) {
	p, ok := principalFromContext(ctx)
	if !ok {
		return "", false
	}
	return p.UserID, true
}

func (s *Server) setSessionCookie(w http.ResponseWriter, token string) {
	http.SetCookie(w, &http.Cookie{
		Name:     sessionCookie,
		Value:    token,
		Path:     "/",
		MaxAge:   int(absoluteTimeout / time.Second),
		HttpOnly: true,
		Secure:   s.cookieSecure,
		SameSite: http.SameSiteStrictMode,
	})
}

func (s *Server) clearSessionCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{
		Name:     sessionCookie,
		Value:    "",
		Path:     "/",
		MaxAge:   -1,
		HttpOnly: true,
		Secure:   s.cookieSecure,
		SameSite: http.SameSiteStrictMode,
	})
}

// sessionTokenHash returns the hash of the request's session cookie, or
// nil when there is no well-formed one.
func sessionTokenHash(r *http.Request) []byte {
	c, err := r.Cookie(sessionCookie)
	if err != nil {
		return nil
	}
	hash, err := auth.HashToken(c.Value)
	if err != nil {
		return nil
	}
	return hash
}

// startSession creates a session for the account and sets its cookie.
func (s *Server) startSession(ctx context.Context, w http.ResponseWriter, r *http.Request, userID string) error {
	token, hash, err := auth.NewToken()
	if err != nil {
		return err
	}
	now := s.now()
	err = s.db.CreateSession(ctx, db.Session{
		TokenHash:  hash,
		UserID:     userID,
		CreatedAt:  now,
		LastSeenAt: now,
		ExpiresAt:  now.Add(absoluteTimeout),
		IP:         clientIP(r),
		UserAgent:  truncate(r.UserAgent(), 512),
	})
	if err != nil {
		return err
	}
	s.setSessionCookie(w, token)
	return nil
}

// clientIP is the address the request came from. Forwarding headers are
// not trusted: nothing in front of the API sets them.
func clientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

// withSession requires a live session and puts its Principal in the
// request context. An expired session is removed; a disabled account's
// session is refused.
// sessionLive reports whether a session is within both its idle and
// absolute timeouts at now.
func sessionLive(sess db.Session, now time.Time) bool {
	return now.Before(sess.ExpiresAt) && now.Sub(sess.LastSeenAt) < idleTimeout
}

func (s *Server) withSession(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		hash := sessionTokenHash(r)
		if hash == nil {
			http.Error(w, "not logged in", http.StatusUnauthorized)
			return
		}
		ctx := r.Context()
		sess, err := s.db.GetSession(ctx, hash)
		if err != nil {
			log.Printf("GetSession error: %v", err)
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}
		if sess == nil {
			s.clearSessionCookie(w)
			http.Error(w, "not logged in", http.StatusUnauthorized)
			return
		}
		now := s.now()
		if !sessionLive(sess.Session, now) || sess.User.Status != db.StatusActive {
			if err := s.db.DeleteSession(ctx, hash); err != nil {
				log.Printf("DeleteSession error: %v", err)
			}
			s.clearSessionCookie(w)
			http.Error(w, "session expired; log in again", http.StatusUnauthorized)
			return
		}
		if now.Sub(sess.LastSeenAt) >= touchEvery {
			if err := s.db.TouchSession(ctx, hash, now); err != nil {
				log.Printf("TouchSession error: %v", err)
			}
		}
		p := &Principal{
			UserID:    sess.User.ID,
			OrgID:     sess.User.OrgID,
			OrgName:   sess.User.OrgName,
			Email:     sess.User.Email,
			FullName:  sess.User.FullName,
			Roles:     sess.User.Roles,
			tokenHash: hash,
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(ctx, principalKey{}, p)))
	}
}

// PurgeExpiredSessions removes sessions that can no longer be used. The
// server runs it at startup and every hour.
func (s *Server) PurgeExpiredSessions(ctx context.Context) {
	n, err := s.db.DeleteExpiredSessions(ctx, s.now(), idleTimeout)
	if err != nil {
		log.Printf("DeleteExpiredSessions error: %v", err)
		return
	}
	if n > 0 {
		log.Printf("removed %d expired sessions", n)
	}
}
