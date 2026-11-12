package httpapi

import (
	"net/http"
	"slices"
	"strings"
	"testing"
	"time"

	"backend/internal/authz"
	"backend/internal/db"
)

const testAdmin = "admin@hospital.test"

// ----- permissions -----

func TestMeListsPermissions(t *testing.T) {
	e := newTestEnv(t)
	_, clinician := e.login("doc@hospital.test")
	_, admin := e.loginWith(testAdmin, db.RoleAdmin)

	me := decodeJSON[meResponse](t, e.do(http.MethodGet, "/auth/me", clinician, nil, ""))
	if !slices.Contains(me.Permissions, authz.DocumentUpload) || slices.Contains(me.Permissions, authz.StaffManage) {
		t.Errorf("clinician permissions = %v", me.Permissions)
	}
	me = decodeJSON[meResponse](t, e.do(http.MethodGet, "/auth/me", admin, nil, ""))
	if !slices.Equal(me.Permissions, []authz.Permission{authz.AuditRead, authz.StaffManage}) {
		t.Errorf("admin permissions = %v", me.Permissions)
	}
}

func TestAdminRoutesAreForbiddenToClinicians(t *testing.T) {
	e := newTestEnv(t)
	_, clinician := e.login("doc@hospital.test")
	other := e.store.addUser("other@hospital.test", db.StatusActive)

	for _, tc := range []struct{ method, path string }{
		{http.MethodGet, "/admin/users"},
		{http.MethodPost, "/admin/users"},
		{http.MethodPatch, "/admin/users/" + other.ID},
		{http.MethodPost, "/admin/users/" + other.ID + "/reset-link"},
		{http.MethodPatch, "/admin/users/not-an-id"}, // 403 before 404: ids aren't probed
	} {
		rec := e.do(tc.method, tc.path, clinician, strings.NewReader(`{"roles":["admin"]}`), "application/json")
		if rec.Code != http.StatusForbidden {
			t.Errorf("%s %s: status = %d, want 403", tc.method, tc.path, rec.Code)
		}
	}
	if u := e.store.user(other.ID); !slices.Equal(u.Roles, []string{db.RoleClinician}) {
		t.Errorf("a clinician changed roles: %v", u.Roles)
	}
}

// Admins manage staff but see no clinical data.
func TestClinicalRoutesAreForbiddenToAdmins(t *testing.T) {
	e := newTestEnv(t)
	adminID, admin := e.loginWith(testAdmin, db.RoleAdmin)
	doc := e.store.addDocument(adminID, "x.pdf")
	body, ct := multipartFile(t, "file", "report.pdf", pdfBytes)

	for _, tc := range []struct{ method, path string }{
		{http.MethodGet, "/documents"},
		{http.MethodGet, "/documents/" + doc + "/insight"},
		{http.MethodPost, "/documents/" + doc + "/insight"},
		{http.MethodPost, "/documents/" + doc + "/reprocess"},
		{http.MethodDelete, "/documents/" + doc},
		{http.MethodGet, "/user/insights"},
		{http.MethodPost, "/user/insights"},
	} {
		rec := e.do(tc.method, tc.path, admin, nil, "")
		if rec.Code != http.StatusForbidden {
			t.Errorf("%s %s: status = %d, want 403", tc.method, tc.path, rec.Code)
		}
	}
	if rec := e.do(http.MethodPost, "/documents", admin, body, ct); rec.Code != http.StatusForbidden {
		t.Errorf("upload: status = %d, want 403", rec.Code)
	}
	if len(e.ai.received()) != 0 {
		t.Error("an admin request reached the AI service")
	}
	if _, ok := e.store.documents[doc]; !ok {
		t.Error("an admin deleted a document")
	}
}

func TestAnAdminWhoIsAlsoAClinicianCanDoBoth(t *testing.T) {
	e := newTestEnv(t)
	_, both := e.loginWith(testAdmin, db.RoleAdmin, db.RoleClinician)
	if rec := e.do(http.MethodGet, "/documents", both, nil, ""); rec.Code != http.StatusOK {
		t.Errorf("documents: status = %d", rec.Code)
	}
	if rec := e.do(http.MethodGet, "/admin/users", both, nil, ""); rec.Code != http.StatusOK {
		t.Errorf("staff: status = %d", rec.Code)
	}
}

func TestUnlistedMethodsAre405(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith(testAdmin, db.RoleAdmin)
	other := e.store.addUser("other@hospital.test", db.StatusActive)
	for _, tc := range []struct{ method, path string }{
		{http.MethodPut, "/admin/users"},
		{http.MethodGet, "/admin/users/" + other.ID},
		{http.MethodGet, "/admin/users/" + other.ID + "/reset-link"},
		{http.MethodPost, "/auth/sessions"},
	} {
		if rec := e.do(tc.method, tc.path, admin, nil, ""); rec.Code != http.StatusMethodNotAllowed {
			t.Errorf("%s %s: status = %d, want 405", tc.method, tc.path, rec.Code)
		}
	}
}

// ----- GET /admin/users -----

func TestListStaffShowsOnlyTheAdminsOrganisation(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith(testAdmin, db.RoleAdmin)
	mine := e.store.addUser("doc@hospital.test", db.StatusInvited)
	e.store.addUserIn("org-2", "elsewhere@other.test", db.StatusActive)

	rec := e.do(http.MethodGet, "/admin/users", admin, nil, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if strings.Contains(rec.Body.String(), "password") {
		t.Error("the staff list mentions passwords")
	}
	staff := decodeJSON[[]staffResponse](t, rec)
	if len(staff) != 2 {
		t.Fatalf("got %d accounts, want 2: %+v", len(staff), staff)
	}
	for _, s := range staff {
		if s.Email == "elsewhere@other.test" {
			t.Error("another organisation's account is listed")
		}
		if s.ID == mine.ID && s.Status != db.StatusInvited {
			t.Errorf("status = %q", s.Status)
		}
	}
}

func TestListStaffShowsALockOnlyWhileItLasts(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith(testAdmin, db.RoleAdmin)
	u := e.store.addUser("doc@hospital.test", db.StatusActive)
	until := e.now().Add(10 * time.Minute)
	e.store.users[u.ID].LockedUntil = &until

	find := func() staffResponse {
		for _, s := range decodeJSON[[]staffResponse](t, e.do(http.MethodGet, "/admin/users", admin, nil, "")) {
			if s.ID == u.ID {
				return s
			}
		}
		t.Fatal("not listed")
		return staffResponse{}
	}
	if got := find(); got.LockedUntil == nil || !got.LockedUntil.Equal(until) {
		t.Errorf("locked_until = %v, want %v", got.LockedUntil, until)
	}
	e.advance(11 * time.Minute)
	if got := find(); got.LockedUntil != nil {
		t.Errorf("an expired lock is shown: %v", got.LockedUntil)
	}
}

// ----- POST /admin/users -----

func TestCreateStaffInvitesWithASetupLink(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith(testAdmin, db.RoleAdmin)

	rec := e.postJSON("/admin/users", admin, map[string]any{
		"email": "  New.Doc@Hospital.TEST ", "full_name": "  Dr New Doc ",
		"registration_no": " MCI-123 ", "roles": []string{"clinician", "clinician"},
	})
	if rec.Code != http.StatusCreated {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	got := decodeJSON[createStaffResponse](t, rec)
	u := got.User
	if u.Email != "new.doc@hospital.test" || u.FullName != "Dr New Doc" || u.RegistrationNo == nil ||
		*u.RegistrationNo != "MCI-123" || !slices.Equal(u.Roles, []string{"clinician"}) || u.Status != db.StatusInvited {
		t.Errorf("user = %+v", u)
	}
	if stored := e.store.user(u.ID); stored.OrgID != "org-1" || stored.PasswordHash != nil {
		t.Errorf("stored = %+v", stored)
	}

	link := got.Link
	if link.Purpose != db.PurposeSetup || !link.ExpiresAt.Equal(e.now().Add(72*time.Hour)) {
		t.Errorf("link = %+v", link)
	}
	prefix := testAppURL + "/set-password#token="
	if !strings.HasPrefix(link.URL, prefix) {
		t.Fatalf("link URL = %q, want prefix %q", link.URL, prefix)
	}

	// The link works: the new account sets a password and logs in.
	token := strings.TrimPrefix(link.URL, prefix)
	if rec := e.postJSON("/auth/setup", "", map[string]string{"token": token, "password": testPassword}); rec.Code != http.StatusNoContent {
		t.Fatalf("setup: status = %d: %s", rec.Code, rec.Body)
	}
	e.loginAs("new.doc@hospital.test", testPassword)
}

func TestCreateStaffRejectsBadInput(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith(testAdmin, db.RoleAdmin)
	e.store.addUserIn("org-2", "taken@other.test", db.StatusActive)

	ok := map[string]any{"email": "a@hospital.test", "full_name": "Dr A", "roles": []string{"clinician"}}
	with := func(k string, v any) map[string]any {
		m := map[string]any{}
		for kk, vv := range ok {
			m[kk] = vv
		}
		m[k] = v
		return m
	}
	for name, tc := range map[string]struct {
		body map[string]any
		want int
	}{
		"bad email":     {with("email", "not-an-email"), http.StatusBadRequest},
		"no name":       {with("full_name", "   "), http.StatusBadRequest},
		"long name":     {with("full_name", strings.Repeat("x", 201)), http.StatusBadRequest},
		"long reg no":   {with("registration_no", strings.Repeat("x", 65)), http.StatusBadRequest},
		"no roles":      {with("roles", []string{}), http.StatusBadRequest},
		"unknown role":  {with("roles", []string{"superuser"}), http.StatusBadRequest},
		"email taken":   {with("email", "TAKEN@other.test"), http.StatusConflict}, // emails are unique across organisations
		"own email too": {with("email", testAdmin), http.StatusConflict},
	} {
		if rec := e.postJSON("/admin/users", admin, tc.body); rec.Code != tc.want {
			t.Errorf("%s: status = %d, want %d: %s", name, rec.Code, tc.want, rec.Body)
		}
	}
	if rec := e.do(http.MethodPost, "/admin/users", admin, strings.NewReader("{"), "application/json"); rec.Code != http.StatusBadRequest {
		t.Errorf("bad JSON: status = %d", rec.Code)
	}
	if n := len(e.store.users); n != 2 {
		t.Errorf("%d accounts stored, want 2", n)
	}
}

// ----- PATCH /admin/users/{id} -----

func TestChangingRolesEndsTheirSessions(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith(testAdmin, db.RoleAdmin)
	docID, doc := e.login("doc@hospital.test")

	rec := e.sendJSON(http.MethodPatch, "/admin/users/"+docID, admin, map[string]any{"roles": []string{"clinician", "admin"}})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if got := decodeJSON[staffResponse](t, rec); !slices.Equal(got.Roles, []string{"clinician", "admin"}) {
		t.Errorf("roles = %v", got.Roles)
	}
	if rec := e.do(http.MethodGet, "/auth/me", doc, nil, ""); rec.Code != http.StatusUnauthorized {
		t.Errorf("their old session still works: %d", rec.Code)
	}
}

func TestChangingOnlyTheNameKeepsTheirSessions(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith(testAdmin, db.RoleAdmin)
	docID, doc := e.login("doc@hospital.test")

	rec := e.sendJSON(http.MethodPatch, "/admin/users/"+docID, admin,
		map[string]any{"full_name": " Dr Renamed ", "registration_no": "R-1", "roles": []string{"clinician"}})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if u := e.store.user(docID); u.FullName != "Dr Renamed" || u.RegistrationNo == nil || *u.RegistrationNo != "R-1" {
		t.Errorf("user = %+v", u)
	}
	if rec := e.do(http.MethodGet, "/auth/me", doc, nil, ""); rec.Code != http.StatusOK {
		t.Errorf("their session ended: %d", rec.Code)
	}

	// An empty registration number clears it.
	e.sendJSON(http.MethodPatch, "/admin/users/"+docID, admin, map[string]any{"registration_no": "  "})
	if u := e.store.user(docID); u.RegistrationNo != nil {
		t.Errorf("registration_no = %q, want none", *u.RegistrationNo)
	}
}

func TestDisablingAUserEndsTheirLiveSession(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith(testAdmin, db.RoleAdmin)
	docID, doc := e.login("doc@hospital.test")
	e.store.addToken(docID, db.PurposeReset, e.now().Add(time.Hour))

	if rec := e.do(http.MethodGet, "/documents", doc, nil, ""); rec.Code != http.StatusOK {
		t.Fatalf("before: status = %d", rec.Code)
	}
	rec := e.sendJSON(http.MethodPatch, "/admin/users/"+docID, admin, map[string]any{"status": "disabled"})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	if rec := e.do(http.MethodGet, "/documents", doc, nil, ""); rec.Code != http.StatusUnauthorized {
		t.Errorf("after: status = %d, want 401", rec.Code)
	}
	if n := e.store.sessionsOf(docID); n != 0 {
		t.Errorf("%d sessions left", n)
	}
	if n := e.store.tokensOf(docID); n != 0 {
		t.Errorf("%d unused links left", n)
	}
	body := map[string]string{"email": "doc@hospital.test", "password": testPassword}
	if rec := e.postJSON("/auth/login", "", body); rec.Code != http.StatusUnauthorized {
		t.Errorf("a disabled account logged in: %d", rec.Code)
	}

	// Enabled again, it can log in.
	if rec := e.sendJSON(http.MethodPatch, "/admin/users/"+docID, admin, map[string]any{"status": "active"}); rec.Code != http.StatusOK {
		t.Fatalf("enable: status = %d", rec.Code)
	}
	e.loginAs("doc@hospital.test", testPassword)
}

func TestEnablingAnAccountWithNoPasswordLeavesItInvited(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith(testAdmin, db.RoleAdmin)
	u := e.store.addUser("new@hospital.test", db.StatusInvited)

	e.sendJSON(http.MethodPatch, "/admin/users/"+u.ID, admin, map[string]any{"status": "disabled"})
	rec := e.sendJSON(http.MethodPatch, "/admin/users/"+u.ID, admin, map[string]any{"status": "active"})
	if got := decodeJSON[staffResponse](t, rec); got.Status != db.StatusInvited {
		t.Errorf("status = %q, want invited", got.Status)
	}
}

func TestAdminsCantLockThemselvesOut(t *testing.T) {
	e := newTestEnv(t)
	adminID, admin := e.loginWith(testAdmin, db.RoleAdmin)
	e.store.addUser("second@hospital.test", db.StatusActive, db.RoleAdmin) // not the last admin either way

	for name, body := range map[string]map[string]any{
		"disable self":     {"status": "disabled"},
		"drop admin role":  {"roles": []string{"clinician"}},
		"bad status value": {"status": "invited"},
	} {
		if rec := e.sendJSON(http.MethodPatch, "/admin/users/"+adminID, admin, body); rec.Code != http.StatusBadRequest {
			t.Errorf("%s: status = %d, want 400", name, rec.Code)
		}
	}
	if u := e.store.user(adminID); u.Status != db.StatusActive || !slices.Equal(u.Roles, []string{db.RoleAdmin}) {
		t.Errorf("the admin's account changed: %+v", u)
	}

	// Adding a role to oneself is fine, and keeps the current session.
	rec := e.sendJSON(http.MethodPatch, "/admin/users/"+adminID, admin, map[string]any{"roles": []string{"admin", "clinician"}})
	if rec.Code != http.StatusOK {
		t.Fatalf("add clinician: status = %d: %s", rec.Code, rec.Body)
	}
	if rec := e.do(http.MethodGet, "/documents", admin, nil, ""); rec.Code != http.StatusOK {
		t.Errorf("the admin's own session ended, or the new role didn't apply: %d", rec.Code)
	}
}

func TestOneAdminCanDisableAnother(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith(testAdmin, db.RoleAdmin)
	second := e.store.addUser("second@hospital.test", db.StatusActive, db.RoleAdmin)
	if rec := e.sendJSON(http.MethodPatch, "/admin/users/"+second.ID, admin, map[string]any{"status": "disabled"}); rec.Code != http.StatusOK {
		t.Errorf("status = %d: %s", rec.Code, rec.Body)
	}
}

// The API can't reach the last-admin rule (the caller is an active admin
// and can't demote themselves), so this drives the store as a race would.
func TestTheLastAdminCantBeRemoved(t *testing.T) {
	e := newTestEnv(t)
	admin := e.store.addUser(testAdmin, db.StatusActive, db.RoleAdmin)
	disabled := db.StatusDisabled
	_, err := e.store.UpdateUser(t.Context(), db.UserUpdate{OrgID: "org-1", UserID: admin.ID, Status: &disabled})
	if err != db.ErrLastAdmin {
		t.Errorf("err = %v, want ErrLastAdmin", err)
	}
}

func TestStaffRoutes404OutsideTheOrganisation(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith(testAdmin, db.RoleAdmin)
	elsewhere := e.store.addUserIn("org-2", "elsewhere@other.test", db.StatusActive)

	for _, path := range []string{
		"/admin/users/" + elsewhere.ID,
		"/admin/users/00000000-0000-4000-8000-999999999999",
		"/admin/users/not-an-id",
		"/admin/users/" + elsewhere.ID + "/nope",
	} {
		if rec := e.sendJSON(http.MethodPatch, path, admin, map[string]any{"status": "disabled"}); rec.Code != http.StatusNotFound {
			t.Errorf("PATCH %s: status = %d, want 404", path, rec.Code)
		}
	}
	if rec := e.postJSON("/admin/users/"+elsewhere.ID+"/reset-link", admin, nil); rec.Code != http.StatusNotFound {
		t.Errorf("reset-link: status = %d, want 404", rec.Code)
	}
	if u := e.store.user(elsewhere.ID); u.Status != db.StatusActive {
		t.Error("another organisation's account changed")
	}
}

// ----- POST /admin/users/{id}/reset-link -----

func TestResetLinks(t *testing.T) {
	e := newTestEnv(t)
	_, admin := e.loginWith(testAdmin, db.RoleAdmin)
	docID, doc := e.login("doc@hospital.test")
	invited := e.store.addUser("new@hospital.test", db.StatusInvited)
	disabled := e.store.addUser("gone@hospital.test", db.StatusDisabled)

	old := e.store.addToken(docID, db.PurposeReset, e.now().Add(time.Hour))
	rec := e.postJSON("/admin/users/"+docID+"/reset-link", admin, nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	link := decodeJSON[linkResponse](t, rec)
	if link.Purpose != db.PurposeReset || !link.ExpiresAt.Equal(e.now().Add(24*time.Hour)) {
		t.Errorf("link = %+v", link)
	}
	if n := e.store.tokensOf(docID); n != 1 {
		t.Errorf("%d unused links, want only the new one", n)
	}
	if rec := e.postJSON("/auth/setup/info", "", map[string]string{"token": old}); rec.Code != http.StatusBadRequest {
		t.Errorf("the old link still works: %d", rec.Code)
	}
	// Issuing a link doesn't log them out; using it does.
	if rec := e.do(http.MethodGet, "/auth/me", doc, nil, ""); rec.Code != http.StatusOK {
		t.Errorf("issuing a link ended their session: %d", rec.Code)
	}

	rec = e.postJSON("/admin/users/"+invited.ID+"/reset-link", admin, nil)
	if got := decodeJSON[linkResponse](t, rec); got.Purpose != db.PurposeSetup || !got.ExpiresAt.Equal(e.now().Add(72*time.Hour)) {
		t.Errorf("invited account's link = %+v", got)
	}
	if rec := e.postJSON("/admin/users/"+disabled.ID+"/reset-link", admin, nil); rec.Code != http.StatusBadRequest {
		t.Errorf("disabled account: status = %d, want 400", rec.Code)
	}
}
