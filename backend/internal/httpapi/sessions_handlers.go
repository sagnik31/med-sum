package httpapi

import (
	"bytes"
	"log"
	"net/http"
	"strings"
	"time"
)

// sessionResponse is one of the caller's sessions. The token hash never
// leaves the server; the id names the session instead.
type sessionResponse struct {
	ID         string    `json:"id"`
	Current    bool      `json:"current"`
	CreatedAt  time.Time `json:"created_at"`
	LastSeenAt time.Time `json:"last_seen_at"`
	ExpiresAt  time.Time `json:"expires_at"`
	IP         string    `json:"ip"`
	UserAgent  string    `json:"user_agent"`
}

// ----- GET and DELETE /auth/sessions -----

// handleSessions lists the caller's live sessions (GET), or ends all of
// them except the one making the request (DELETE).
func (s *Server) handleSessions(w http.ResponseWriter, r *http.Request) {
	p, _ := principalFromContext(r.Context())
	ctx := r.Context()
	switch r.Method {
	case http.MethodGet:
		sessions, err := s.db.ListUserSessions(ctx, p.UserID)
		if err != nil {
			log.Printf("ListUserSessions error: %v", err)
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}
		now := s.now()
		out := []sessionResponse{}
		for _, x := range sessions {
			if !sessionLive(x, now) {
				continue // waiting for the hourly purge
			}
			out = append(out, sessionResponse{
				ID: x.ID, Current: bytes.Equal(x.TokenHash, p.tokenHash),
				CreatedAt: x.CreatedAt, LastSeenAt: x.LastSeenAt, ExpiresAt: x.ExpiresAt,
				IP: x.IP, UserAgent: x.UserAgent,
			})
		}
		writeJSON(w, http.StatusOK, out)
	case http.MethodDelete:
		if err := s.db.DeleteUserSessions(ctx, p.UserID, p.tokenHash); err != nil {
			log.Printf("DeleteUserSessions error: %v", err)
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// ----- DELETE /auth/sessions/{id} -----

// handleSessionByID ends one of the caller's sessions. Someone else's
// session, or an unknown id, is 404. Ending the current session logs out.
func (s *Server) handleSessionByID(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodDelete {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	id := strings.TrimPrefix(r.URL.Path, "/auth/sessions/")
	if !validUUID(id) {
		http.NotFound(w, r)
		return
	}
	p, _ := principalFromContext(r.Context())
	ctx := r.Context()

	// Find out first whether it is the current session, to clear the cookie.
	sessions, err := s.db.ListUserSessions(ctx, p.UserID)
	if err != nil {
		log.Printf("ListUserSessions error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	current := false
	for _, x := range sessions {
		if x.ID == id {
			current = bytes.Equal(x.TokenHash, p.tokenHash)
		}
	}
	deleted, err := s.db.DeleteUserSession(ctx, p.UserID, id)
	if err != nil {
		log.Printf("DeleteUserSession error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if !deleted {
		http.NotFound(w, r)
		return
	}
	if current {
		s.clearSessionCookie(w)
	}
	w.WriteHeader(http.StatusNoContent)
}
