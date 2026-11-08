package httpapi

import (
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"time"

	"backend/internal/auth"
	"backend/internal/authz"
	"backend/internal/db"
)

// Login lockout.
const (
	maxLoginFailures = 5
	lockDuration     = 15 * time.Minute
	maxAuthBody      = 4 << 10 // an auth request is a few short fields
)

// errLoginFailed is the one answer to every failed login, so a response
// never tells whether an email has an account, or whether it is locked.
const errLoginFailed = "invalid email or password; after 5 failed attempts an account is locked for 15 minutes"

func decodeAuthBody(w http.ResponseWriter, r *http.Request, v any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, maxAuthBody)
	if err := json.NewDecoder(r.Body).Decode(v); err != nil {
		http.Error(w, "invalid JSON body", http.StatusBadRequest)
		return false
	}
	return true
}

type orgResponse struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

// meResponse is the logged-in account as the frontend sees it. The
// frontend shows or hides pages by permissions; the API enforces them.
type meResponse struct {
	ID          string             `json:"id"`
	Email       string             `json:"email"`
	FullName    string             `json:"full_name"`
	Roles       []string           `json:"roles"`
	Permissions []authz.Permission `json:"permissions"`
	Org         orgResponse        `json:"org"`
}

func meFromUser(u *db.User) meResponse {
	return meResponse{ID: u.ID, Email: u.Email, FullName: u.FullName, Roles: u.Roles,
		Permissions: authz.Permissions(u.Roles), Org: orgResponse{ID: u.OrgID, Name: u.OrgName}}
}

// ----- POST /auth/login -----

type loginRequest struct {
	Email    string `json:"email"`
	Password string `json:"password"`
}

func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	var req loginRequest
	if !decodeAuthBody(w, r, &req) {
		return
	}
	if req.Email == "" || req.Password == "" {
		http.Error(w, "email and password are required", http.StatusBadRequest)
		return
	}
	fail := func() { http.Error(w, errLoginFailed, http.StatusUnauthorized) }
	if len(req.Password) > 4*auth.MaxPasswordLen {
		fail() // never hash an oversized input
		return
	}
	email, err := auth.NormalizeEmail(req.Email)
	if err != nil {
		s.hasher.VerifyDummy(req.Password)
		fail()
		return
	}

	ctx := r.Context()
	user, err := s.db.GetUserByEmail(ctx, email)
	if err != nil {
		log.Printf("GetUserByEmail error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	now := s.now()
	// Unknown, not yet set up, disabled or locked: spend the same time as
	// a real check, and say the same thing. A locked account's password is
	// not checked at all, so guessing stops while it is locked.
	if user == nil || user.Status != db.StatusActive || user.PasswordHash == nil ||
		(user.LockedUntil != nil && now.Before(*user.LockedUntil)) {
		s.hasher.VerifyDummy(req.Password)
		fail()
		return
	}

	ok, err := s.hasher.Verify(req.Password, *user.PasswordHash)
	if err != nil {
		log.Printf("password hash of user %s is unreadable: %v", user.ID, err)
	}
	if !ok {
		if _, err := s.db.RecordLoginFailure(ctx, user.ID, maxLoginFailures, now.Add(lockDuration)); err != nil {
			log.Printf("RecordLoginFailure error: %v", err)
		}
		fail()
		return
	}

	if err := s.db.RecordLoginSuccess(ctx, user.ID, now); err != nil {
		log.Printf("RecordLoginSuccess error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if s.hasher.NeedsRehash(*user.PasswordHash) {
		if hash, err := s.hasher.Hash(req.Password); err == nil {
			if err := s.db.SetPasswordHash(ctx, user.ID, hash, now); err != nil {
				log.Printf("SetPasswordHash (rehash) error: %v", err)
			}
		}
	}
	if err := s.startSession(ctx, w, r, user.ID); err != nil {
		log.Printf("startSession error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusOK, meFromUser(user))
}

// ----- POST /auth/logout -----

// handleLogout ends the request's session, if it has one. It always
// succeeds, so a client can call it to be sure it is logged out.
func (s *Server) handleLogout(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	if hash := sessionTokenHash(r); hash != nil {
		if err := s.db.DeleteSession(r.Context(), hash); err != nil {
			log.Printf("DeleteSession error: %v", err)
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}
	}
	s.clearSessionCookie(w)
	w.WriteHeader(http.StatusNoContent)
}

// ----- GET /auth/me -----

func (s *Server) handleMe(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	p, _ := principalFromContext(r.Context())
	writeJSON(w, http.StatusOK, meResponse{ID: p.UserID, Email: p.Email, FullName: p.FullName,
		Roles: p.Roles, Permissions: authz.Permissions(p.Roles), Org: orgResponse{ID: p.OrgID, Name: p.OrgName}})
}

// ----- POST /auth/password -----

type changePasswordRequest struct {
	CurrentPassword string `json:"current_password"`
	NewPassword     string `json:"new_password"`
}

// handleChangePassword changes the caller's password. The current
// password is required (a session alone is not enough), a wrong one counts
// towards the lockout, and every other session of the account ends.
func (s *Server) handleChangePassword(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	var req changePasswordRequest
	if !decodeAuthBody(w, r, &req) {
		return
	}
	p, _ := principalFromContext(r.Context())
	ctx := r.Context()
	user, err := s.db.GetUserByID(ctx, p.UserID)
	if err != nil || user == nil || user.PasswordHash == nil {
		log.Printf("GetUserByID error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	now := s.now()
	if len(req.CurrentPassword) > 4*auth.MaxPasswordLen {
		http.Error(w, "the current password is wrong", http.StatusForbidden)
		return
	}
	ok, _ := s.hasher.Verify(req.CurrentPassword, *user.PasswordHash)
	if !ok {
		if _, err := s.db.RecordLoginFailure(ctx, user.ID, maxLoginFailures, now.Add(lockDuration)); err != nil {
			log.Printf("RecordLoginFailure error: %v", err)
		}
		http.Error(w, "the current password is wrong", http.StatusForbidden)
		return
	}
	if req.NewPassword == req.CurrentPassword {
		http.Error(w, "the new password must be different", http.StatusBadRequest)
		return
	}
	if err := auth.CheckPassword(req.NewPassword, user.Email); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	hash, err := s.hasher.Hash(req.NewPassword)
	if err != nil {
		log.Printf("Hash error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if err := s.db.SetPasswordHash(ctx, user.ID, hash, now); err != nil {
		log.Printf("SetPasswordHash error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if err := s.db.DeleteUserSessions(ctx, user.ID, p.tokenHash); err != nil {
		log.Printf("DeleteUserSessions error: %v", err)
	}
	w.WriteHeader(http.StatusNoContent)
}

// ----- POST /auth/setup/info and POST /auth/setup -----

type setupInfoRequest struct {
	Token string `json:"token"`
}

type setupInfoResponse struct {
	Email    string `json:"email"`
	FullName string `json:"full_name"`
	Org      string `json:"org"`
}

// setupUser returns the account a setup or reset token is for, or writes
// a 400 and returns nil.
func (s *Server) setupUser(w http.ResponseWriter, r *http.Request, token string) (*db.User, []byte) {
	hash, err := auth.HashToken(token)
	if err != nil {
		http.Error(w, db.ErrTokenUnusable.Error(), http.StatusBadRequest)
		return nil, nil
	}
	user, err := s.db.GetUsableToken(r.Context(), hash, s.now())
	if err != nil {
		log.Printf("GetUsableToken error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return nil, nil
	}
	if user == nil {
		http.Error(w, db.ErrTokenUnusable.Error(), http.StatusBadRequest)
		return nil, nil
	}
	return user, hash
}

// handleSetupInfo tells the set-password page whose account a link is
// for. The token travels in the body, not the URL, so it stays out of
// logs.
func (s *Server) handleSetupInfo(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	var req setupInfoRequest
	if !decodeAuthBody(w, r, &req) {
		return
	}
	user, _ := s.setupUser(w, r, req.Token)
	if user == nil {
		return
	}
	writeJSON(w, http.StatusOK, setupInfoResponse{Email: user.Email, FullName: user.FullName, Org: user.OrgName})
}

type setupRequest struct {
	Token    string `json:"token"`
	Password string `json:"password"`
}

// handleSetup sets a password from a setup or reset link. The account is
// activated if it was invited, and its existing sessions end. It does not
// log the user in: they log in with the new password.
func (s *Server) handleSetup(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	var req setupRequest
	if !decodeAuthBody(w, r, &req) {
		return
	}
	user, hash := s.setupUser(w, r, req.Token)
	if user == nil {
		return
	}
	if err := auth.CheckPassword(req.Password, user.Email); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	passwordHash, err := s.hasher.Hash(req.Password)
	if err != nil {
		log.Printf("Hash error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if _, err := s.db.CompletePasswordSetup(r.Context(), hash, passwordHash, s.now()); err != nil {
		if errors.Is(err, db.ErrTokenUnusable) {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		log.Printf("CompletePasswordSetup error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
