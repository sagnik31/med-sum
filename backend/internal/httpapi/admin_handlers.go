package httpapi

import (
	"errors"
	"log"
	"net/http"
	"slices"
	"strings"
	"time"
	"unicode/utf8"

	"backend/internal/auth"
	"backend/internal/authz"
	"backend/internal/db"
)

// Limits on staff details.
const (
	maxFullName       = 200
	maxRegistrationNo = 64
)

// staffResponse is an account as the staff page sees it.
type staffResponse struct {
	ID             string     `json:"id"`
	Email          string     `json:"email"`
	FullName       string     `json:"full_name"`
	RegistrationNo *string    `json:"registration_no"`
	Roles          []string   `json:"roles"`
	Status         string     `json:"status"`
	LockedUntil    *time.Time `json:"locked_until"` // set only while locked
	CreatedAt      time.Time  `json:"created_at"`
	LastLoginAt    *time.Time `json:"last_login_at"`
}

func (s *Server) staffFromUser(u *db.User) staffResponse {
	out := staffResponse{ID: u.ID, Email: u.Email, FullName: u.FullName, RegistrationNo: u.RegistrationNo,
		Roles: u.Roles, Status: u.Status, CreatedAt: u.CreatedAt, LastLoginAt: u.LastLoginAt}
	if u.LockedUntil != nil && s.now().Before(*u.LockedUntil) {
		out.LockedUntil = u.LockedUntil
	}
	return out
}

// linkResponse carries a setup or reset link for the admin to pass on.
type linkResponse struct {
	Purpose   string    `json:"purpose"` // "setup" or "reset"
	URL       string    `json:"url"`
	ExpiresAt time.Time `json:"expires_at"`
}

// issueLink stores a new one-time token for the account, replacing its
// unused ones. An invited account gets a setup link, an active one a reset
// link.
func (s *Server) issueLink(r *http.Request, u *db.User, adminID string) (*linkResponse, error) {
	token, hash, err := auth.NewToken()
	if err != nil {
		return nil, err
	}
	purpose, ttl := db.PurposeReset, auth.ResetLinkTTL
	if u.Status == db.StatusInvited {
		purpose, ttl = db.PurposeSetup, auth.SetupLinkTTL
	}
	now := s.now()
	err = s.db.CreateOneTimeToken(r.Context(), db.OneTimeToken{
		TokenHash: hash, UserID: u.ID, Purpose: purpose, CreatedAt: now, ExpiresAt: now.Add(ttl), CreatedBy: &adminID,
	})
	if err != nil {
		return nil, err
	}
	return &linkResponse{Purpose: purpose, URL: auth.SetPasswordLink(s.appURL, token), ExpiresAt: now.Add(ttl)}, nil
}

// cleanRoles checks roles from a request: at least one, all known. It
// returns them without duplicates.
func cleanRoles(roles []string) ([]string, error) {
	if len(roles) == 0 {
		return nil, errors.New("an account needs at least one role")
	}
	out := []string{}
	for _, r := range roles {
		if !authz.KnownRole(r) {
			return nil, errors.New("unknown role: " + r)
		}
		if !slices.Contains(out, r) {
			out = append(out, r)
		}
	}
	return out, nil
}

func cleanFullName(name string) (string, error) {
	name = strings.TrimSpace(name)
	if name == "" {
		return "", errors.New("the full name is required")
	}
	if utf8.RuneCountInString(name) > maxFullName {
		return "", errors.New("the full name is too long")
	}
	return name, nil
}

// cleanRegistrationNo trims a registration number; "" means none.
func cleanRegistrationNo(no string) (string, error) {
	no = strings.TrimSpace(no)
	if utf8.RuneCountInString(no) > maxRegistrationNo {
		return "", errors.New("the registration number is too long")
	}
	return no, nil
}

// ----- GET /admin/users -----

func (s *Server) handleListStaff(w http.ResponseWriter, r *http.Request) {
	p, _ := principalFromContext(r.Context())
	users, err := s.db.ListUsers(r.Context(), p.OrgID)
	if err != nil {
		log.Printf("ListUsers error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	out := make([]staffResponse, 0, len(users))
	for i := range users {
		out = append(out, s.staffFromUser(&users[i]))
	}
	writeJSON(w, http.StatusOK, out)
}

// ----- POST /admin/users -----

type createStaffRequest struct {
	Email          string   `json:"email"`
	FullName       string   `json:"full_name"`
	RegistrationNo string   `json:"registration_no"`
	Roles          []string `json:"roles"`
}

type createStaffResponse struct {
	User staffResponse `json:"user"`
	Link linkResponse  `json:"link"`
}

// handleCreateStaff adds an account to the admin's organisation, invited
// and without a password, and returns its setup link for the admin to
// pass on.
func (s *Server) handleCreateStaff(w http.ResponseWriter, r *http.Request) {
	var req createStaffRequest
	if !decodeAuthBody(w, r, &req) {
		return
	}
	email, err := auth.NormalizeEmail(req.Email)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	name, err := cleanFullName(req.FullName)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	regNo, err := cleanRegistrationNo(req.RegistrationNo)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	roles, err := cleanRoles(req.Roles)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	p, _ := principalFromContext(r.Context())
	nu := db.NewUser{OrgID: p.OrgID, Email: email, FullName: name, Roles: roles, CreatedBy: &p.UserID}
	if regNo != "" {
		nu.RegistrationNo = &regNo
	}
	user, err := s.db.CreateUser(r.Context(), nu)
	if errors.Is(err, db.ErrEmailTaken) {
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}
	if err != nil {
		log.Printf("CreateUser error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	link, err := s.issueLink(r, user, p.UserID)
	if err != nil {
		// The account exists; the admin can issue a link from the staff page.
		log.Printf("issueLink error: %v", err)
		http.Error(w, "the account was created but its setup link wasn't; issue a new link", http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusCreated, createStaffResponse{User: s.staffFromUser(user), Link: *link})
}

// ----- PATCH /admin/users/{id} and POST /admin/users/{id}/reset-link -----

func (s *Server) handleStaffByID(w http.ResponseWriter, r *http.Request) {
	// The permission comes first, so a clinician learns nothing about
	// which ids exist.
	if !permit(w, r, authz.StaffManage) {
		return
	}
	rest := strings.TrimPrefix(r.URL.Path, "/admin/users/")
	id, action, _ := strings.Cut(rest, "/")
	if !validUUID(id) {
		http.NotFound(w, r)
		return
	}
	switch {
	case action == "" && r.Method == http.MethodPatch:
		s.handleUpdateStaff(w, r, id)
	case action == "reset-link" && r.Method == http.MethodPost:
		s.handleStaffLink(w, r, id)
	case action == "" || action == "reset-link":
		w.WriteHeader(http.StatusMethodNotAllowed)
	default:
		http.NotFound(w, r)
	}
}

type updateStaffRequest struct {
	Roles          []string `json:"roles"`
	Status         *string  `json:"status"`
	FullName       *string  `json:"full_name"`
	RegistrationNo *string  `json:"registration_no"`
}

// handleUpdateStaff changes an account's roles, status, name or
// registration number. Admins can't disable themselves or drop their own
// admin role, and an organisation always keeps one active admin.
func (s *Server) handleUpdateStaff(w http.ResponseWriter, r *http.Request, id string) {
	var req updateStaffRequest
	if !decodeAuthBody(w, r, &req) {
		return
	}
	p, _ := principalFromContext(r.Context())
	self := id == p.UserID
	upd := db.UserUpdate{OrgID: p.OrgID, UserID: id}

	if req.Roles != nil {
		roles, err := cleanRoles(req.Roles)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if self && !slices.Contains(roles, authz.RoleAdmin) {
			http.Error(w, "you can't remove your own admin role; ask another admin", http.StatusBadRequest)
			return
		}
		upd.Roles = roles
	}
	if req.Status != nil {
		if *req.Status != db.StatusActive && *req.Status != db.StatusDisabled {
			http.Error(w, `status must be "active" or "disabled"`, http.StatusBadRequest)
			return
		}
		if self && *req.Status == db.StatusDisabled {
			http.Error(w, "you can't disable your own account; ask another admin", http.StatusBadRequest)
			return
		}
		upd.Status = req.Status
	}
	if req.FullName != nil {
		name, err := cleanFullName(*req.FullName)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		upd.FullName = &name
	}
	if req.RegistrationNo != nil {
		no, err := cleanRegistrationNo(*req.RegistrationNo)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		upd.RegistrationNo = &no
	}
	if self {
		upd.KeepSession = p.tokenHash
	}

	user, err := s.db.UpdateUser(r.Context(), upd)
	if errors.Is(err, db.ErrLastAdmin) {
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}
	if err != nil {
		log.Printf("UpdateUser error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if user == nil {
		http.NotFound(w, r) // no such account, or another organisation's
		return
	}
	writeJSON(w, http.StatusOK, s.staffFromUser(user))
}

// handleStaffLink issues a new setup link (invited account) or reset link
// (active account). Earlier unused links stop working.
func (s *Server) handleStaffLink(w http.ResponseWriter, r *http.Request, id string) {
	p, _ := principalFromContext(r.Context())
	user, err := s.db.GetUserByID(r.Context(), id)
	if err != nil {
		log.Printf("GetUserByID error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if user == nil || user.OrgID != p.OrgID {
		http.NotFound(w, r)
		return
	}
	if user.Status == db.StatusDisabled {
		http.Error(w, "the account is disabled; enable it first", http.StatusBadRequest)
		return
	}
	link, err := s.issueLink(r, user, p.UserID)
	if err != nil {
		log.Printf("issueLink error: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusOK, link)
}
