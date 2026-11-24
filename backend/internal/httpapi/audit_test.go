package httpapi

import (
	"encoding/json"
	"errors"
	"net/http"
	"slices"
	"strings"
	"testing"
	"time"

	"backend/internal/db"
)

// newEvents returns the events written since the store held n of them.
func (f *fakeStore) newEvents(n int) []db.AuditEvent {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]db.AuditEvent(nil), f.audit[n:]...)
}

func (f *fakeStore) eventCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.audit)
}

// auditFixture is a clinician's patient with one document of theirs, with
// a record, a summary and a patient name and file name that must never
// reach the audit log.
type auditFixture struct {
	e        *testEnv
	me, auth string
	patient  *db.Patient
	doc      string
}

const (
	secretName = "Asha Verma"
	secretMRN  = "SH-SECRET-77"
	secretFile = "asha-verma-cbc.pdf"
)

func newAuditFixture(t *testing.T) *auditFixture {
	t.Helper()
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	p, err := e.store.CreatePatient(t.Context(), "org-1", me, db.PatientFields{MRN: secretMRN, FullName: secretName}, nil)
	if err != nil {
		t.Fatal(err)
	}
	d, err := e.store.CreateDocument(t.Context(), "org-1", db.NewDocument{PatientID: p.ID, UploadedBy: me,
		OriginalName: secretFile, ContentType: "application/pdf", StoragePath: "a.pdf"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	addRecord(e.store, d.ID, testRecord)
	html, status := "<p>"+secretName+"</p>", "completed"
	e.store.summaries[p.ID] = &db.PatientSummary{HTML: &html, Status: &status}
	return &auditFixture{e: e, me: me, auth: auth, patient: p, doc: d.ID}
}

// assertNoClinicalContent fails when an event carries the patient's name,
// MRN or file name, or the record's values.
func assertNoClinicalContent(t *testing.T, events []db.AuditEvent) {
	t.Helper()
	b, _ := json.Marshal(events)
	for _, secret := range []string{secretName, secretMRN, secretFile, "Asha", "9.1", "Hb"} {
		if strings.Contains(string(b), secret) {
			t.Errorf("an audit event carries %q: %s", secret, b)
		}
	}
}

// Every route that reads or changes patient data writes exactly one event,
// naming the actor, the organisation, the target and the patient.
func TestEachPatientRouteWritesOneAuditEvent(t *testing.T) {
	type call struct {
		name, method, path string
		body               any
		action, target     string
	}
	calls := func(fx *auditFixture) []call {
		p, d := fx.patient.ID, fx.doc
		return []call{
			{"list", http.MethodGet, "/patients?q=asha", nil, actPatientsListed, ""},
			{"create", http.MethodPost, "/patients", map[string]any{"mrn": "SH-NEW", "full_name": secretName + " II"}, actPatientCreated, "patient"},
			{"view", http.MethodGet, "/patients/" + p, nil, actPatientViewed, "patient"},
			{"update", http.MethodPatch, "/patients/" + p, map[string]any{"full_name": secretName + " K"}, actPatientUpdated, "patient"},
			{"documents", http.MethodGet, "/patients/" + p + "/documents", nil, actDocumentsListed, "patient"},
			{"summary", http.MethodGet, "/patients/" + p + "/summary", nil, actSummaryViewed, "patient"},
			{"build summary", http.MethodPost, "/patients/" + p + "/summary", nil, actSummaryRequested, "patient"},
			{"document", http.MethodGet, "/documents/" + d, nil, actDocumentViewed, "document"},
			{"insight", http.MethodGet, "/documents/" + d + "/insight", nil, actInsightViewed, "document"},
			{"process", http.MethodPost, "/documents/" + d + "/insight", nil, actInsightRequested, "document"},
			{"record", http.MethodGet, "/documents/" + d + "/record", nil, actRecordViewed, "document"},
			{"reprocess", http.MethodPost, "/documents/" + d + "/reprocess", nil, actDocumentReprocess, "document"},
			{"delete", http.MethodDelete, "/documents/" + d, nil, actDocumentDeleted, "document"},
		}
	}
	for _, c := range calls(newAuditFixture(t)) {
		t.Run(c.name, func(t *testing.T) {
			fx := newAuditFixture(t)
			c := calls(fx)[slices.IndexFunc(calls(fx), func(x call) bool { return x.name == c.name })]
			before := fx.e.store.eventCount()

			rec := fx.e.sendJSON(c.method, c.path, fx.auth, c.body)
			if rec.Code >= 300 {
				t.Fatalf("status %d: %s", rec.Code, rec.Body)
			}
			events := fx.e.store.newEvents(before)
			if len(events) != 1 {
				t.Fatalf("events = %+v, want one", events)
			}
			ev := events[0]
			if ev.Action != c.action || ev.Outcome != db.OutcomeSuccess || deref(ev.ActorID) != fx.me ||
				deref(ev.OrgID) != "org-1" || deref(ev.TargetType) != c.target || ev.IP == "" {
				t.Errorf("event = %+v", ev)
			}
			if c.target != "" && ev.TargetID == nil {
				t.Error("the event names no target")
			}
			if c.name != "list" && c.name != "create" && deref(ev.PatientID) != fx.patient.ID {
				t.Errorf("patient = %q, want %q", deref(ev.PatientID), fx.patient.ID)
			}
			assertNoClinicalContent(t, events)
		})
	}
}

func TestUploadWritesOneAuditEvent(t *testing.T) {
	fx := newAuditFixture(t)
	body, ct := multipartFile(t, "file", secretFile, pdfBytes)
	before := fx.e.store.eventCount()

	rec := fx.e.do(http.MethodPost, "/patients/"+fx.patient.ID+"/documents", fx.auth, body, ct)

	if rec.Code != http.StatusCreated {
		t.Fatalf("status %d: %s", rec.Code, rec.Body)
	}
	events := fx.e.store.newEvents(before)
	doc := decodeJSON[db.Document](t, rec)
	if len(events) != 1 || events[0].Action != actDocumentUploaded || deref(events[0].TargetID) != doc.ID ||
		deref(events[0].PatientID) != fx.patient.ID {
		t.Errorf("events = %+v", events)
	}
	assertNoClinicalContent(t, events)
}

func TestAPatientUpdateNamesTheChangedFieldsOnly(t *testing.T) {
	fx := newAuditFixture(t)
	before := fx.e.store.eventCount()

	fx.e.sendJSON(http.MethodPatch, "/patients/"+fx.patient.ID, fx.auth,
		map[string]any{"full_name": "Asha V. Verma", "sex": "F", "mrn": secretMRN})

	events := fx.e.store.newEvents(before)
	got, _ := json.Marshal(events[0].Details["fields"])
	if string(got) != `["full_name","sex"]` {
		t.Errorf("fields = %s", got)
	}
	assertNoClinicalContent(t, events)
}

// A read that can't be recorded is refused: the data is never sent.
func TestAReadThatCantBeAuditedIsRefused(t *testing.T) {
	for _, path := range []string{"/patients", "/patients/%p", "/patients/%p/documents", "/patients/%p/summary",
		"/documents/%d", "/documents/%d/insight", "/documents/%d/record"} {
		t.Run(path, func(t *testing.T) {
			fx := newAuditFixture(t)
			fx.e.store.failAudit = errors.New("audit table unavailable")
			path := strings.NewReplacer("%p", fx.patient.ID, "%d", fx.doc).Replace(path)

			rec := fx.e.do(http.MethodGet, path, fx.auth, nil, "")

			if rec.Code != http.StatusInternalServerError {
				t.Errorf("status = %d, want 500", rec.Code)
			}
			if strings.Contains(rec.Body.String(), "Verma") || strings.Contains(rec.Body.String(), fx.doc) {
				t.Errorf("data was sent: %s", rec.Body)
			}
		})
	}
}

// A change that can't be recorded doesn't happen, and nothing is asked of
// the AI service.
func TestAChangeThatCantBeAuditedDoesNotHappen(t *testing.T) {
	fx := newAuditFixture(t)
	e := fx.e
	e.store.failAudit = errors.New("audit table unavailable")
	patients := len(e.store.patients)

	checks := []struct {
		name string
		rec  func() int
	}{
		{"create", func() int {
			return e.postJSON("/patients", fx.auth, map[string]any{"mrn": "SH-NEW", "full_name": "N"}).Code
		}},
		{"update", func() int {
			return e.sendJSON(http.MethodPatch, "/patients/"+fx.patient.ID, fx.auth, map[string]any{"full_name": "N"}).Code
		}},
		{"delete", func() int { return e.do(http.MethodDelete, "/documents/"+fx.doc, fx.auth, nil, "").Code }},
		{"summary", func() int { return e.do(http.MethodPost, "/patients/"+fx.patient.ID+"/summary", fx.auth, nil, "").Code }},
		{"reprocess", func() int { return e.do(http.MethodPost, "/documents/"+fx.doc+"/reprocess", fx.auth, nil, "").Code }},
	}
	for _, c := range checks {
		if code := c.rec(); code != http.StatusInternalServerError {
			t.Errorf("%s: status = %d, want 500", c.name, code)
		}
	}
	body, ct := multipartFile(t, "file", "b.pdf", pdfBytes)
	if rec := e.do(http.MethodPost, "/patients/"+fx.patient.ID+"/documents", fx.auth, body, ct); rec.Code != http.StatusInternalServerError {
		t.Errorf("upload: status = %d, want 500", rec.Code)
	}

	if len(e.store.patients) != patients || e.store.patients[fx.patient.ID].FullName != secretName {
		t.Error("a patient was created or changed")
	}
	if len(e.store.deleted) != 0 || len(e.store.documents) != 1 {
		t.Error("a document was deleted or added")
	}
	if calls := e.ai.received(); len(calls) != 0 {
		t.Errorf("the AI service was called: %+v", calls)
	}
}

func TestARefusedDeleteIsAudited(t *testing.T) {
	fx := newAuditFixture(t)
	_, other := fx.e.login("other@hospital.test")
	before := fx.e.store.eventCount()

	rec := fx.e.do(http.MethodDelete, "/documents/"+fx.doc, other, nil, "")

	events := fx.e.store.newEvents(before)
	if rec.Code != http.StatusForbidden || len(events) != 1 || events[0].Action != actDocumentDeleted ||
		events[0].Outcome != db.OutcomeDenied || events[0].Details["reason"] != "not_uploader" {
		t.Errorf("%d %+v", rec.Code, events)
	}
}

func TestAMissingPermissionIsAudited(t *testing.T) {
	e := newTestEnv(t)
	admin, auth := e.loginWith("admin@hospital.test", db.RoleAdmin)
	before := e.store.eventCount()

	rec := e.do(http.MethodGet, "/patients", auth, nil, "")

	events := e.store.newEvents(before)
	if rec.Code != http.StatusForbidden || len(events) != 1 {
		t.Fatalf("%d %+v", rec.Code, events)
	}
	ev := events[0]
	if ev.Action != actAccessDenied || ev.Outcome != db.OutcomeDenied || deref(ev.ActorID) != admin ||
		ev.Details["permission"] != "patient.read" || ev.Details["path"] != "/patients" {
		t.Errorf("event = %+v", ev)
	}
}

// ----- logins, logouts and account changes -----

func TestLoginsAreAudited(t *testing.T) {
	e := newTestEnv(t)
	u := e.store.addUser("me@hospital.test", db.StatusActive)

	e.loginAs("me@hospital.test", testPassword)
	login := func(email, password string) {
		body, _ := json.Marshal(map[string]string{"email": email, "password": password})
		e.do(http.MethodPost, "/auth/login", "", strings.NewReader(string(body)), "application/json")
	}
	login("me@hospital.test", "the wrong password")
	login("nobody@hospital.test", "the wrong password")

	events := e.store.newEvents(0)
	if got := e.store.auditActions(); !slices.Equal(got, []string{actLoginSucceeded, actLoginFailed, actLoginFailed}) {
		t.Fatalf("actions = %v", got)
	}
	if deref(events[0].ActorID) != u.ID || deref(events[0].OrgID) != "org-1" {
		t.Errorf("success = %+v", events[0])
	}
	if events[1].Outcome != db.OutcomeFailure || deref(events[1].ActorID) != u.ID || events[1].Details["reason"] != "wrong_password" {
		t.Errorf("wrong password = %+v", events[1])
	}
	unknown := events[2]
	if unknown.ActorID != nil || unknown.OrgID != nil || unknown.Details["reason"] != "unknown_account" {
		t.Errorf("unknown account = %+v", unknown)
	}
	b, _ := json.Marshal(events)
	if strings.Contains(string(b), "nobody") || strings.Contains(string(b), "wrong password") {
		t.Errorf("a login event keeps the email or the password: %s", b)
	}
}

func TestALockoutIsAudited(t *testing.T) {
	e := newTestEnv(t)
	e.store.addUser("me@hospital.test", db.StatusActive)
	for range maxLoginFailures {
		body := strings.NewReader(`{"email":"me@hospital.test","password":"the wrong password"}`)
		e.do(http.MethodPost, "/auth/login", "", body, "application/json")
	}
	body := strings.NewReader(`{"email":"me@hospital.test","password":"` + testPassword + `"}`)
	e.do(http.MethodPost, "/auth/login", "", body, "application/json")

	got := e.store.auditActions()
	want := append(slices.Repeat([]string{actLoginFailed}, maxLoginFailures), db.ActionAccountLocked, actLoginFailed)
	if !slices.Equal(got, want) {
		t.Fatalf("actions = %v, want %v", got, want)
	}
	if last := e.store.newEvents(len(got) - 1)[0]; last.Details["reason"] != "locked" {
		t.Errorf("a login while locked = %+v", last)
	}
}

func TestALoginThatCantBeAuditedFails(t *testing.T) {
	e := newTestEnv(t)
	e.store.addUser("me@hospital.test", db.StatusActive)
	e.store.failAudit = errors.New("audit table unavailable")

	body := strings.NewReader(`{"email":"me@hospital.test","password":"` + testPassword + `"}`)
	rec := e.do(http.MethodPost, "/auth/login", "", body, "application/json")

	if rec.Code != http.StatusInternalServerError || sessionCookieFrom(t, rec) != nil {
		t.Errorf("status %d, cookie %v", rec.Code, sessionCookieFrom(t, rec))
	}
}

func TestLogoutAndSessionEndsAreAudited(t *testing.T) {
	e := newTestEnv(t)
	me, first := e.login("me@hospital.test")
	second := e.loginAs("me@hospital.test", testPassword)
	before := e.store.eventCount()

	e.do(http.MethodDelete, "/auth/sessions", second, nil, "")
	e.do(http.MethodPost, "/auth/logout", second, nil, "")
	e.do(http.MethodPost, "/auth/logout", first, nil, "") // already ended: nothing to record

	events := e.store.newEvents(before)
	if len(events) != 2 || events[0].Action != actSessionsEnded || events[1].Action != actLogout ||
		deref(events[1].ActorID) != me {
		t.Errorf("events = %+v", events)
	}
}

func TestStaffChangesAreAudited(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.loginWith("admin@hospital.test", db.RoleAdmin)
	before := e.store.eventCount()

	rec := e.postJSON("/admin/users", auth, map[string]any{"email": "new@hospital.test", "full_name": "Dr New",
		"roles": []string{"clinician"}})
	created := decodeJSON[createStaffResponse](t, rec)
	e.sendJSON(http.MethodPatch, "/admin/users/"+created.User.ID, auth, map[string]any{"status": "disabled", "full_name": "Dr N"})

	events := e.store.newEvents(before)
	actions := []string{}
	for _, ev := range events {
		actions = append(actions, ev.Action)
		if deref(ev.TargetID) != created.User.ID {
			t.Errorf("%s targets %q", ev.Action, deref(ev.TargetID))
		}
	}
	if !slices.Equal(actions, []string{actStaffInvited, actLinkIssued, actStaffUpdated}) {
		t.Fatalf("actions = %v", actions)
	}
	if upd := events[2].Details; upd["status"] != "disabled" || upd["fields"] == nil {
		t.Errorf("update details = %v", upd)
	}
	b, _ := json.Marshal(events)
	if strings.Contains(string(b), "new@hospital.test") || strings.Contains(string(b), "Dr N") {
		t.Errorf("a staff event keeps the email or the name: %s", b)
	}
}

func TestPasswordChangesAreAudited(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	before := e.store.eventCount()

	e.postJSON("/auth/password", auth, map[string]string{"current_password": testPassword,
		"new_password": "another long test password"})
	token := e.store.addToken(me, db.PurposeReset, e.now().Add(time.Hour))
	e.postJSON("/auth/setup", "", map[string]string{"token": token, "password": "a third long test password"})

	events := e.store.newEvents(before)
	if len(events) != 2 || events[0].Action != actPasswordChanged || events[1].Action != actPasswordSet ||
		deref(events[1].ActorID) != me || deref(events[1].OrgID) != "org-1" {
		t.Errorf("events = %+v", events)
	}
}

// ----- GET /admin/audit -----

func TestAdminsReadTheirOrganisationsAuditLog(t *testing.T) {
	fx := newAuditFixture(t)
	e := fx.e
	e.do(http.MethodGet, "/patients/"+fx.patient.ID, fx.auth, nil, "")
	them := e.store.addUserIn("org-2", "them@other.test", db.StatusActive)
	other := "org-2"
	e.store.audit = append(e.store.audit, db.AuditEvent{ID: 999, OrgID: &other, ActorID: &them.ID, Action: "patient_viewed", Outcome: "success"})
	_, admin := e.loginWith("admin@hospital.test", db.RoleAdmin)

	rec := e.do(http.MethodGet, "/admin/audit", admin, nil, "")

	if rec.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rec.Code, rec.Body)
	}
	page := decodeJSON[auditPage](t, rec)
	if len(page.Events) == 0 || page.NextBefore != nil {
		t.Fatalf("page = %+v", page)
	}
	for _, ev := range page.Events {
		if deref(ev.ActorID) == them.ID {
			t.Error("another organisation's event is listed")
		}
	}
	if page.Events[0].Action != actLoginSucceeded { // the admin's own login; newest first
		t.Errorf("first = %+v", page.Events[0])
	}
	if strings.Contains(rec.Body.String(), "org_id") {
		t.Error("the response carries the organisation id")
	}
	if got := e.store.auditActions(); got[len(got)-1] != actAuditViewed {
		t.Errorf("reading the log wasn't recorded: %v", got)
	}
}

func TestTheAuditLogFilters(t *testing.T) {
	fx := newAuditFixture(t)
	e := fx.e
	e.do(http.MethodGet, "/patients/"+fx.patient.ID, fx.auth, nil, "")
	_, admin := e.loginWith("admin@hospital.test", db.RoleAdmin)

	byPatient := decodeJSON[auditPage](t, e.do(http.MethodGet, "/admin/audit?patient="+fx.patient.ID, admin, nil, ""))
	if len(byPatient.Events) != 1 || byPatient.Events[0].Action != actPatientViewed {
		t.Errorf("by patient = %+v", byPatient.Events)
	}
	byActor := decodeJSON[auditPage](t, e.do(http.MethodGet, "/admin/audit?actor="+fx.me, admin, nil, ""))
	for _, ev := range byActor.Events {
		if deref(ev.ActorID) != fx.me {
			t.Errorf("by actor lists %+v", ev)
		}
	}
	day := e.now().Format(time.DateOnly)
	if got := decodeJSON[auditPage](t, e.do(http.MethodGet, "/admin/audit?from="+day+"&to="+day, admin, nil, "")); len(got.Events) == 0 {
		t.Error("an event of today is outside from=today&to=today")
	}
	// Times: the last one is exclusive, so a day is its start to the next's.
	at := func(d time.Duration) string { return e.now().UTC().Add(d).Format(time.RFC3339) }
	if got := decodeJSON[auditPage](t, e.do(http.MethodGet, "/admin/audit?from="+at(-time.Hour)+"&to="+at(time.Hour), admin, nil, "")); len(got.Events) == 0 {
		t.Error("an event of now is outside the hour either side")
	}
	if got := decodeJSON[auditPage](t, e.do(http.MethodGet, "/admin/audit?to="+at(-time.Hour), admin, nil, "")); len(got.Events) != 0 {
		t.Errorf("until an hour ago = %+v", got.Events)
	}
	tomorrow := e.now().AddDate(0, 0, 1).Format(time.DateOnly)
	if got := decodeJSON[auditPage](t, e.do(http.MethodGet, "/admin/audit?from="+tomorrow, admin, nil, "")); len(got.Events) != 0 {
		t.Errorf("from tomorrow = %+v", got.Events)
	}
	for _, q := range []string{"actor=x", "patient=1", "from=12/11/2026", "before=-1", "before=abc"} {
		if rec := e.do(http.MethodGet, "/admin/audit?"+q, admin, nil, ""); rec.Code != http.StatusBadRequest {
			t.Errorf("%s: status = %d", q, rec.Code)
		}
	}
}

func TestCliniciansCantReadTheAuditLog(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	if rec := e.do(http.MethodGet, "/admin/audit", auth, nil, ""); rec.Code != http.StatusForbidden {
		t.Errorf("status = %d", rec.Code)
	}
}
