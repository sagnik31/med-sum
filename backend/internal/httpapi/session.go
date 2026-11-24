package httpapi

import (
	"context"
	"log"
	"net"
	"net/http"
	"net/netip"
	"strings"
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

	userAgentMaxSize = 512
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

// newSession is a session for the account, to store, and the token its
// cookie carries.
func (s *Server) newSession(r *http.Request, userID string) (db.Session, string, error) {
	token, hash, err := auth.NewToken()
	if err != nil {
		return db.Session{}, "", err
	}
	now := s.now()
	return db.Session{
		TokenHash:  hash,
		UserID:     userID,
		CreatedAt:  now,
		LastSeenAt: now,
		ExpiresAt:  now.Add(absoluteTimeout),
		IP:         s.clientIP(r),
		UserAgent:  userAgent(r),
	}, token, nil
}

// clientIP is the address the request came from. Forwarding headers are
// not trusted: nothing in front of the API sets them.
// clientIP is the address a request came from, for the login rate limit,
// sessions and the audit log. Behind a trusted proxy every request's own
// address is the proxy's, which would put every user in one rate-limit
// bucket (anyone's failed logins locking everyone out) and every audit row
// at the proxy, so a request from one is taken to come from the last
// address in X-Forwarded-For that is not itself a trusted proxy: the one
// the nearest proxy saw. The header is ignored from anyone else, since a
// client can send any value.
func (s *Server) clientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	addr, err := netip.ParseAddr(host)
	if err != nil || !s.trustedProxies[addr.Unmap()] {
		return host
	}
	hops := strings.Split(strings.Join(r.Header.Values("X-Forwarded-For"), ","), ",")
	for i := len(hops) - 1; i >= 0; i-- {
		hop, err := netip.ParseAddr(strings.TrimSpace(hops[i]))
		if err != nil {
			return host // a hop the proxy didn't write; trust no further
		}
		if !s.trustedProxies[hop.Unmap()] {
			return hop.Unmap().String()
		}
	}
	return host
}

// userAgent is the request's User-Agent as sessions and the audit log store
// it: at most userAgentMaxSize bytes of valid UTF-8. A header can hold any
// bytes, and the database refuses text that isn't UTF-8, which would fail
// the login or the audit row the header came with.
func userAgent(r *http.Request) string {
	ua := strings.ToValidUTF8(r.UserAgent(), "")
	if len(ua) > userAgentMaxSize {
		ua = strings.ToValidUTF8(ua[:userAgentMaxSize], "") // drops a character cut in half
	}
	return ua
}

// withSession requires a live session and puts its Principal in the
// request context. An expired session is removed; a disabled account's
// session is refused.
// sessionLive reports whether a session is within both its idle and
// absolute timeouts at now.
func sessionLive(sess db.Session, now time.Time) bool {
	return now.Before(sess.ExpiresAt) && now.Sub(sess.LastSeenAt) < idleTimeout
}

// backgroundHeader marks a poll the page makes on its own (a document or a
// summary being processed). Its session is checked as on any request, but
// the poll is not activity: it doesn't move last_seen_at, so a screen left
// open on a long job still logs out after the idle timeout. See
// docs/adr/0010-staff-access-and-security.md.
const backgroundHeader = "X-Medsum-Background"

func isBackground(r *http.Request) bool { return r.Header.Get(backgroundHeader) == "1" }

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
			if err := s.db.DeleteSession(ctx, hash, nil); err != nil {
				log.Printf("DeleteSession error: %v", err)
			}
			s.clearSessionCookie(w)
			http.Error(w, "session expired; log in again", http.StatusUnauthorized)
			return
		}
		lastSeen := sess.LastSeenAt
		if now.Sub(sess.LastSeenAt) >= touchEvery && !isBackground(r) {
			if err := s.db.TouchSession(ctx, hash, now); err != nil {
				log.Printf("TouchSession error: %v", err)
			} else {
				lastSeen = now
			}
		}
		setSessionExpires(w, sess.Session, lastSeen, now)
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
