package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"io"
	"log"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"backend/internal/auth"
	"backend/internal/db"
)

const (
	testInternalToken = "test-internal-token"
	testOrigin        = "http://localhost:5173"
	testPassword      = "a long test password"
	testAppURL        = "https://app.test"
)

// testHasher uses tiny argon2 parameters so the tests stay fast.
var testHasher = func() *auth.Hasher {
	h, err := auth.NewHasher(auth.Params{Memory: 64, Time: 1, Threads: 1, SaltLen: 16, KeyLen: 32})
	if err != nil {
		panic(err)
	}
	return h
}()

// TestMain hides the handlers' error logs, which the tests trigger on
// purpose, unless the tests run with -v.
func TestMain(m *testing.M) {
	flag.Parse()
	if !testing.Verbose() {
		log.SetOutput(io.Discard)
	}
	os.Exit(m.Run())
}

// fakeStore is an in-memory Store. Users and documents are keyed by ID;
// sessions and one-time tokens by their hash; insights by document ID;
// patients and their summaries by patient ID.
type fakeStore struct {
	mu         sync.Mutex
	nextID     int
	users      map[string]*db.User
	sessions   map[string]*db.Session
	tokens     map[string]*fakeToken
	patients   map[string]*db.Patient
	documents  map[string]*db.Document
	deleted    map[string]string // soft-deleted document ID -> who deleted it
	insights   map[string]*db.Insight
	records    map[string]*db.DocumentRecord
	summaries  map[string]*db.PatientSummary
	flagged    map[string]int // document ID -> values awaiting review, for the queue
	reviews    []db.RecordReview
	processing map[string]*db.ProcessingRun
	traces     map[string]*db.DocumentTrace // document ID -> its runs and steps
	orgRuns    map[string][]db.OrgRun       // org ID -> its runs, newest first
	orgSteps   map[string][]db.OrgStep      // run ID -> its steps
	auditOpen  map[string]bool              // document ID -> an open audit sample
	auditToGo  map[string]int               // document ID -> values not yet reviewed in its audit
	auditLeft  map[string][]string          // document ID -> the holders GetOpenAudit names as not yet reviewed
	auditSince map[string]time.Time         // document ID -> when its open audit sample was taken
	auditDone  map[string]time.Time         // document ID -> when CompleteAudit finished its audit
	failWith   error                        // returned by every method when set
	// audit holds the events written, in order. failAudit fails every
	// audit write; a change that comes with an event then changes nothing,
	// as its transaction would roll back.
	audit     []db.AuditEvent
	failAudit error
	// failCreateDocument is returned by CreateDocument alone, so an upload
	// gets as far as writing its file before the database fails.
	failCreateDocument error
}

type fakeToken struct {
	db.OneTimeToken
	UsedAt *time.Time
}

func newFakeStore() *fakeStore {
	return &fakeStore{
		users:      map[string]*db.User{},
		sessions:   map[string]*db.Session{},
		tokens:     map[string]*fakeToken{},
		patients:   map[string]*db.Patient{},
		documents:  map[string]*db.Document{},
		deleted:    map[string]string{},
		insights:   map[string]*db.Insight{},
		records:    map[string]*db.DocumentRecord{},
		summaries:  map[string]*db.PatientSummary{},
		flagged:    map[string]int{},
		processing: map[string]*db.ProcessingRun{},
		traces:     map[string]*db.DocumentTrace{},
		orgRuns:    map[string][]db.OrgRun{},
		orgSteps:   map[string][]db.OrgStep{},
		auditOpen:  map[string]bool{},
		auditToGo:  map[string]int{},
		auditLeft:  map[string][]string{},
		auditSince: map[string]time.Time{},
	}
}

// newID returns a valid, increasing UUID so routes that parse IDs accept it.
func (f *fakeStore) newID() string {
	f.nextID++
	return "00000000-0000-4000-8000-" + leftPad(f.nextID)
}

func leftPad(n int) string {
	s := []byte("000000000000")
	for i := len(s) - 1; n > 0; i-- {
		s[i] = byte('0' + n%10)
		n /= 10
	}
	return string(s)
}

// ----- accounts -----

// addUser stores an account in the test organisation, "org-1". An active
// account gets testPassword. With no roles it is a clinician.
func (f *fakeStore) addUser(email, status string, roles ...string) *db.User {
	return f.addUserIn("org-1", email, status, roles...)
}

// addUserIn stores an account in the organisation orgID.
func (f *fakeStore) addUserIn(orgID, email, status string, roles ...string) *db.User {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(roles) == 0 {
		roles = []string{db.RoleClinician}
	}
	orgName := "Test Hospital"
	if orgID != "org-1" {
		orgName = "Hospital " + orgID
	}
	u := &db.User{ID: f.newID(), OrgID: orgID, OrgName: orgName, Email: email,
		FullName: "Dr " + email, Roles: roles, Status: status, CreatedAt: time.Now()}
	if status != db.StatusInvited {
		hash, err := testHasher.Hash(testPassword)
		if err != nil {
			panic(err)
		}
		u.PasswordHash = &hash
	}
	f.users[u.ID] = u
	copied := *u
	return &copied
}

func (f *fakeStore) user(id string) *db.User {
	f.mu.Lock()
	defer f.mu.Unlock()
	copied := *f.users[id]
	return &copied
}

func (f *fakeStore) GetUserByEmail(_ context.Context, email string) (*db.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	for _, u := range f.users {
		if u.Email == email {
			copied := *u
			return &copied, nil
		}
	}
	return nil, nil
}

func (f *fakeStore) GetUserByID(_ context.Context, id string) (*db.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	u, ok := f.users[id]
	if !ok {
		return nil, nil
	}
	copied := *u
	return &copied, nil
}

func (f *fakeStore) GetOrgUser(_ context.Context, orgID, id string) (*db.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	u, ok := f.users[id]
	if !ok || u.OrgID != orgID {
		return nil, nil
	}
	copied := *u
	return &copied, nil
}

func (f *fakeStore) RecordLoginFailure(_ context.Context, userID string, maxFailures int, lockUntil time.Time, ev *db.AuditEvent) (*time.Time, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	if ev != nil && f.failAudit != nil {
		return nil, f.failAudit
	}
	u := f.users[userID]
	u.FailedLogins++
	f.logAudit(ev)
	if u.FailedLogins >= maxFailures {
		u.FailedLogins = 0
		u.LockedUntil = &lockUntil
		if ev != nil {
			lock := *ev
			lock.Action = db.ActionAccountLocked
			f.logAudit(&lock)
		}
	}
	return u.LockedUntil, nil
}

func (f *fakeStore) RecordLoginSuccess(_ context.Context, userID string, now time.Time, ev *db.AuditEvent) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return f.failWith
	}
	if ev != nil && f.failAudit != nil {
		return f.failAudit
	}
	u := f.users[userID]
	u.FailedLogins, u.LockedUntil, u.LastLoginAt = 0, nil, &now
	f.logAudit(ev)
	return nil
}

func (f *fakeStore) SetPasswordHash(_ context.Context, userID, hash string, _ time.Time, ev *db.AuditEvent) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return f.failWith
	}
	if ev != nil && f.failAudit != nil {
		return f.failAudit
	}
	f.users[userID].PasswordHash = &hash
	f.logAudit(ev)
	return nil
}

// ----- sessions -----

func (f *fakeStore) CreateSession(_ context.Context, sess db.Session) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return f.failWith
	}
	copied := sess
	if copied.ID == "" {
		copied.ID = f.newID()
	}
	f.sessions[string(sess.TokenHash)] = &copied
	return nil
}

func (f *fakeStore) GetSession(_ context.Context, tokenHash []byte) (*db.SessionWithUser, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	sess, ok := f.sessions[string(tokenHash)]
	if !ok {
		return nil, nil
	}
	return &db.SessionWithUser{Session: *sess, User: *f.users[sess.UserID]}, nil
}

func (f *fakeStore) TouchSession(_ context.Context, tokenHash []byte, now time.Time) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return f.failWith
	}
	if sess, ok := f.sessions[string(tokenHash)]; ok {
		sess.LastSeenAt = now
	}
	return nil
}

func (f *fakeStore) DeleteSession(_ context.Context, tokenHash []byte, ev *db.AuditEvent) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return f.failWith
	}
	if ev != nil && f.failAudit != nil {
		return f.failAudit
	}
	sess, ok := f.sessions[string(tokenHash)]
	if !ok {
		return nil
	}
	delete(f.sessions, string(tokenHash))
	if ev != nil {
		u := f.users[sess.UserID]
		ev.ActorID, ev.OrgID = &u.ID, &u.OrgID
		f.logAudit(ev)
	}
	return nil
}

func (f *fakeStore) DeleteUserSessions(_ context.Context, userID string, keep []byte, ev *db.AuditEvent) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return f.failWith
	}
	if ev != nil && f.failAudit != nil {
		return f.failAudit
	}
	for k, sess := range f.sessions {
		if sess.UserID == userID && (keep == nil || k != string(keep)) {
			delete(f.sessions, k)
		}
	}
	f.logAudit(ev)
	return nil
}

func (f *fakeStore) DeleteExpiredSessions(_ context.Context, now time.Time, idle time.Duration) (int64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return 0, f.failWith
	}
	var n int64
	for k, sess := range f.sessions {
		if !now.Before(sess.ExpiresAt) || !sess.LastSeenAt.After(now.Add(-idle)) {
			delete(f.sessions, k)
			n++
		}
	}
	return n, nil
}

func (f *fakeStore) sessionsOf(userID string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := 0
	for _, sess := range f.sessions {
		if sess.UserID == userID {
			n++
		}
	}
	return n
}

// ----- one-time tokens -----

// addToken stores a setup or reset token for the account and returns the
// token a link would carry.
func (f *fakeStore) addToken(userID, purpose string, expiresAt time.Time) string {
	token, hash, err := auth.NewToken()
	if err != nil {
		panic(err)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.tokens[string(hash)] = &fakeToken{OneTimeToken: db.OneTimeToken{
		TokenHash: hash, UserID: userID, Purpose: purpose, CreatedAt: time.Now(), ExpiresAt: expiresAt}}
	return token
}

func (f *fakeStore) usable(tokenHash []byte, now time.Time) (*fakeToken, *db.User) {
	t, ok := f.tokens[string(tokenHash)]
	if !ok || t.UsedAt != nil || !now.Before(t.ExpiresAt) {
		return nil, nil
	}
	u := f.users[t.UserID]
	if u.Status == db.StatusDisabled {
		return nil, nil
	}
	return t, u
}

func (f *fakeStore) GetUsableToken(_ context.Context, tokenHash []byte, now time.Time) (*db.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	_, u := f.usable(tokenHash, now)
	if u == nil {
		return nil, nil
	}
	copied := *u
	return &copied, nil
}

func (f *fakeStore) CompletePasswordSetup(_ context.Context, tokenHash []byte, passwordHash string, now time.Time, ev *db.AuditEvent) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return "", f.failWith
	}
	if ev != nil && f.failAudit != nil {
		return "", f.failAudit
	}
	t, u := f.usable(tokenHash, now)
	if u == nil {
		return "", db.ErrTokenUnusable
	}
	t.UsedAt = &now
	u.PasswordHash = &passwordHash
	if u.Status == db.StatusInvited {
		u.Status = db.StatusActive
	}
	u.FailedLogins, u.LockedUntil = 0, nil
	for k, sess := range f.sessions {
		if sess.UserID == u.ID {
			delete(f.sessions, k)
		}
	}
	if ev != nil {
		id, org := u.ID, u.OrgID
		ev.ActorID, ev.OrgID, ev.TargetID = &id, &org, &id
		f.logAudit(ev)
	}
	return u.ID, nil
}

// ----- the audit log -----

// logAudit keeps an event; call it holding f.mu. nil writes nothing.
func (f *fakeStore) logAudit(ev *db.AuditEvent) {
	if ev == nil {
		return
	}
	e := *ev
	e.ID = int64(len(f.audit) + 1)
	f.audit = append(f.audit, e)
}

func (f *fakeStore) RecordAudit(_ context.Context, ev db.AuditEvent) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return f.failWith
	}
	if f.failAudit != nil {
		return f.failAudit
	}
	f.logAudit(&ev)
	return nil
}

func (f *fakeStore) ListAuditEvents(_ context.Context, orgID string, filter db.AuditFilter) ([]db.AuditEvent, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	out := []db.AuditEvent{}
	for i := len(f.audit) - 1; i >= 0 && len(out) < filter.Limit; i-- {
		e := f.audit[i]
		switch {
		case e.OrgID == nil || *e.OrgID != orgID,
			filter.ActorID != "" && (e.ActorID == nil || *e.ActorID != filter.ActorID),
			filter.PatientID != "" && (e.PatientID == nil || *e.PatientID != filter.PatientID) &&
				!listsPatient(e.Details, filter.PatientID),
			filter.From != nil && e.At.Before(*filter.From),
			filter.To != nil && !e.At.Before(*filter.To),
			filter.BeforeID > 0 && e.ID >= filter.BeforeID:
			continue
		}
		out = append(out, e)
	}
	return out, nil
}

// auditActions lists the actions of the events written so far.
func (f *fakeStore) auditActions() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := []string{}
	for _, e := range f.audit {
		out = append(out, e.Action)
	}
	return out
}

// ----- patients, documents and summaries -----
//
// Like the real store, every lookup goes through the patient's
// organisation, so the handler tests see the same 404s for another
// organisation's rows; deleted documents are kept but never returned.

// addPatient stores a patient in the organisation and returns it.
func (f *fakeStore) addPatient(orgID, mrn string) *db.Patient {
	p, err := f.CreatePatient(context.Background(), orgID, "", db.PatientFields{MRN: mrn, FullName: "Patient " + mrn}, nil)
	if err != nil {
		panic(err)
	}
	return p
}

// patientIn returns the stored patient if it is in the organisation;
// call it holding f.mu.
func (f *fakeStore) patientIn(orgID, patientID string) *db.Patient {
	p, ok := f.patients[patientID]
	if !ok || p.OrgID != orgID {
		return nil
	}
	return p
}

func (f *fakeStore) CreatePatient(_ context.Context, orgID, createdBy string, pf db.PatientFields, ev *db.AuditEvent) (*db.Patient, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	if ev != nil && f.failAudit != nil {
		return nil, f.failAudit
	}
	for _, p := range f.patients {
		if p.OrgID == orgID && p.MRN == pf.MRN {
			return nil, db.ErrMRNTaken
		}
	}
	now := time.Now()
	p := &db.Patient{ID: f.newID(), OrgID: orgID, MRN: pf.MRN, FullName: pf.FullName,
		DateOfBirth: pf.DateOfBirth, AgeYears: pf.AgeYears, Sex: pf.Sex, CreatedAt: now, UpdatedAt: now}
	if createdBy != "" {
		p.CreatedBy = &createdBy
	}
	f.patients[p.ID] = p
	if ev != nil {
		ev.TargetID, ev.PatientID = &p.ID, &p.ID
		f.logAudit(ev)
	}
	copied := *p
	return &copied, nil
}

func (f *fakeStore) UpdatePatient(_ context.Context, orgID, patientID string, pf db.PatientFields, ev *db.AuditEvent) (*db.Patient, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	if ev != nil && f.failAudit != nil {
		return nil, f.failAudit
	}
	p := f.patientIn(orgID, patientID)
	if p == nil {
		return nil, nil
	}
	for _, o := range f.patients {
		if o.ID != p.ID && o.OrgID == orgID && o.MRN == pf.MRN {
			return nil, db.ErrMRNTaken
		}
	}
	p.MRN, p.FullName, p.DateOfBirth, p.AgeYears, p.Sex = pf.MRN, pf.FullName, pf.DateOfBirth, pf.AgeYears, pf.Sex
	p.UpdatedAt = time.Now()
	f.logAudit(ev)
	copied := *p
	return &copied, nil
}

func (f *fakeStore) GetPatient(_ context.Context, orgID, patientID string) (*db.Patient, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	p := f.patientIn(orgID, patientID)
	if p == nil {
		return nil, nil
	}
	copied := *p
	return &copied, nil
}

func (f *fakeStore) ListPatients(_ context.Context, orgID, query string) ([]db.Patient, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	q := strings.ToLower(strings.TrimSpace(query))
	out := []db.Patient{}
	for _, p := range f.patients {
		if p.OrgID != orgID {
			continue
		}
		if q == "" || strings.Contains(strings.ToLower(p.FullName), q) || strings.HasPrefix(strings.ToLower(p.MRN), q) {
			out = append(out, *p)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].FullName < out[j].FullName })
	return out, nil
}

func (f *fakeStore) GetPatientSummary(_ context.Context, patientID string) (*db.PatientSummary, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	if s, ok := f.summaries[patientID]; ok {
		copied := *s
		return &copied, nil
	}
	return &db.PatientSummary{}, nil
}

// duplicateOf is the first other live document of d's patient with d's
// file hash (IDs increase, so the smallest earlier ID); call it holding f.mu.
func (f *fakeStore) duplicateOf(d *db.Document) *string {
	var first *string
	for _, o := range f.documents {
		if _, gone := f.deleted[o.ID]; gone {
			continue
		}
		if o.PatientID == d.PatientID && o.SHA256 != "" && o.SHA256 == d.SHA256 && o.ID < d.ID &&
			(first == nil || o.ID < *first) {
			id := o.ID
			first = &id
		}
	}
	return first
}

// documentIn returns the stored live document if its patient is in the
// organisation; call it holding f.mu.
func (f *fakeStore) documentIn(orgID, docID string) *db.Document {
	d, ok := f.documents[docID]
	if !ok || f.patientIn(orgID, d.PatientID) == nil {
		return nil
	}
	if _, gone := f.deleted[docID]; gone {
		return nil
	}
	return d
}

func (f *fakeStore) CreateDocument(_ context.Context, orgID string, nd db.NewDocument, ev *db.AuditEvent) (*db.Document, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	if ev != nil && f.failAudit != nil {
		return nil, f.failAudit
	}
	if f.failCreateDocument != nil {
		return nil, f.failCreateDocument
	}
	if f.patientIn(orgID, nd.PatientID) == nil {
		return nil, nil
	}
	now := time.Now()
	d := &db.Document{
		ID:           f.newID(),
		PatientID:    nd.PatientID,
		OriginalName: nd.OriginalName,
		ContentType:  nd.ContentType,
		StoragePath:  nd.StoragePath,
		UploadedAt:   now,
		UpdatedAt:    now,
		SHA256:       nd.SHA256,
	}
	if nd.UploadedBy != "" {
		by := nd.UploadedBy
		d.UploadedBy = &by
		if u, ok := f.users[by]; ok {
			name := u.FullName
			d.UploadedByName = &name
		}
	}
	f.documents[d.ID] = d
	if ev != nil {
		ev.TargetID, ev.PatientID = &d.ID, &d.PatientID
		f.logAudit(ev)
	}
	copied := *d
	copied.DuplicateOf = f.duplicateOf(d)
	return &copied, nil
}

func (f *fakeStore) GetDocument(_ context.Context, orgID, docID string) (*db.Document, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	d := f.documentIn(orgID, docID)
	if d == nil {
		return nil, nil
	}
	copied := *d
	copied.DuplicateOf = f.duplicateOf(d)
	return &copied, nil
}

func (f *fakeStore) ListPatientDocuments(_ context.Context, orgID, patientID string) ([]db.Document, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	docs := []db.Document{}
	for id, d := range f.documents {
		if d.PatientID != patientID || f.documentIn(orgID, id) == nil {
			continue
		}
		copied := *d
		copied.DuplicateOf = f.duplicateOf(d)
		copied.InsightStatus = "not_started"
		if ins, ok := f.insights[d.ID]; ok {
			copied.InsightStatus = ins.Status
		}
		docs = append(docs, copied)
	}
	sort.Slice(docs, func(i, j int) bool { return docs[i].ID > docs[j].ID })
	return docs, nil
}

func (f *fakeStore) SoftDeleteDocument(_ context.Context, orgID, docID, deletedBy string, _ time.Time, ev *db.AuditEvent) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return false, f.failWith
	}
	if ev != nil && f.failAudit != nil {
		return false, f.failAudit
	}
	if f.documentIn(orgID, docID) == nil {
		return false, nil
	}
	f.deleted[docID] = deletedBy
	f.logAudit(ev)
	return true, nil
}

func (f *fakeStore) GetInsightByDocumentID(_ context.Context, docID string) (*db.Insight, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	if ins, ok := f.insights[docID]; ok {
		copied := *ins
		return &copied, nil
	}
	return nil, nil
}

func (f *fakeStore) GetDocumentRecord(_ context.Context, docID string) (*db.DocumentRecord, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	if rec, ok := f.records[docID]; ok {
		copied := *rec
		return &copied, nil
	}
	return nil, nil
}

// GetOpenAudit answers from auditOpen, auditSince and auditLeft.
func (f *fakeStore) GetOpenAudit(_ context.Context, docID string) (*db.OpenAudit, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	if !f.auditOpen[docID] {
		return nil, nil
	}
	return &db.OpenAudit{SampledAt: f.auditSince[docID], Unreviewed: f.auditLeft[docID]}, nil
}

// CompleteAudit closes the open audit sample the test set in auditOpen.
func (f *fakeStore) CompleteAudit(_ context.Context, orgID, docID, _ string, at time.Time, ev *db.AuditEvent) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return f.failWith
	}
	if f.documentIn(orgID, docID) == nil || !f.auditOpen[docID] {
		return db.ErrNoOpenAudit
	}
	if n := f.auditToGo[docID]; n > 0 {
		return &db.AuditIncompleteError{Unreviewed: n}
	}
	if f.failAudit != nil {
		return f.failAudit
	}
	f.auditOpen[docID] = false
	if f.auditDone == nil {
		f.auditDone = map[string]time.Time{}
	}
	f.auditDone[docID] = at
	if ins := f.insights[docID]; ins != nil && ins.Status == "checks_passed" {
		ins.Status = "verified"
	}
	f.logAudit(ev)
	return nil
}

// SignOff follows the real method's rules: a checks_passed live document
// of the organisation, not an open audit sample and with its record
// unchanged since recordUpdatedAt, becomes verified.
func (f *fakeStore) SignOff(_ context.Context, orgID, docID string, recordUpdatedAt, at time.Time, ev *db.AuditEvent) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return f.failWith
	}
	ins := f.insights[docID]
	if f.documentIn(orgID, docID) == nil || ins == nil || ins.Status != "checks_passed" {
		return db.ErrNotAwaitingSignOff
	}
	if f.auditOpen[docID] {
		return db.ErrAuditBeforeSignOff
	}
	if rec := f.records[docID]; rec == nil || !rec.UpdatedAt.Equal(recordUpdatedAt) {
		return db.ErrReviewConflict
	}
	if f.failAudit != nil {
		return f.failAudit
	}
	ins.Status = "verified"
	ins.UpdatedAt = &at
	f.logAudit(ev)
	return nil
}

// GetDocumentTrace answers from traces: every run, and the one runID names
// (the first, i.e. latest, when runID is "") with the steps stored for it.
func (f *fakeStore) GetDocumentTrace(_ context.Context, docID, runID string) (*db.DocumentTrace, bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, false, f.failWith
	}
	t := f.traces[docID]
	if t == nil {
		t = &db.DocumentTrace{Runs: []db.TraceRun{}, Steps: []db.TraceStep{}, Prompts: map[string]db.TracePrompt{}}
	}
	out := *t
	out.Run = nil
	for i := range t.Runs {
		if runID == "" || t.Runs[i].RunID == runID {
			out.Run = &t.Runs[i]
			break
		}
	}
	if out.Run == nil {
		out.Steps = []db.TraceStep{}
		return &out, runID == "", nil
	}
	return &out, true, nil
}

// ListOrgRuns answers from orgRuns, before `before` when set.
func (f *fakeStore) ListOrgRuns(_ context.Context, orgID string, before *time.Time) ([]db.OrgRun, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	out := []db.OrgRun{}
	for _, r := range f.orgRuns[orgID] {
		if before == nil || r.StartedAt.Before(*before) {
			out = append(out, r)
		}
	}
	return out, nil
}

// GetOrgRun answers from orgRuns and orgSteps; another organisation's run is not found.
func (f *fakeStore) GetOrgRun(_ context.Context, orgID, runID string) (*db.OrgRun, []db.OrgStep, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, nil, f.failWith
	}
	for _, r := range f.orgRuns[orgID] {
		if r.RunID == runID {
			run := r
			return &run, f.orgSteps[runID], nil
		}
	}
	return nil, nil, nil
}

// GetProcessing returns what the test stored in processing.
func (f *fakeStore) GetProcessing(_ context.Context, docID string) (*db.ProcessingRun, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	return f.processing[docID], nil
}

// ListReviewQueue lists the organisation's documents in needs_review or
// checks_passed (audit when auditOpen), oldest first, with the flagged
// count the test set in flagged.
func (f *fakeStore) ListReviewQueue(_ context.Context, orgID string) ([]db.ReviewQueueEntry, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	out := []db.ReviewQueueEntry{}
	for id, d := range f.documents {
		ins := f.insights[id]
		p := f.patients[d.PatientID]
		if ins == nil || p == nil || p.OrgID != orgID {
			continue
		}
		reason := ins.Status
		if ins.Status == "checks_passed" && f.auditOpen[id] {
			reason = "audit"
		} else if ins.Status != "needs_review" && ins.Status != "checks_passed" {
			continue
		}
		e := db.ReviewQueueEntry{DocumentID: id, PatientID: p.ID, PatientName: p.FullName,
			PatientMRN: p.MRN, OriginalName: d.OriginalName, UploadedAt: d.UploadedAt,
			Flagged: f.flagged[id], Reason: reason}
		if ins.UpdatedAt != nil {
			e.QueuedAt = *ins.UpdatedAt
		}
		out = append(out, e)
	}
	sort.Slice(out, func(i, j int) bool {
		if !out[i].QueuedAt.Equal(out[j].QueuedAt) {
			return out[i].QueuedAt.Before(out[j].QueuedAt)
		}
		return out[i].DocumentID < out[j].DocumentID
	})
	return out, nil
}

// ApplyReview follows the real method's rules: the document must be the
// organisation's and live, its processing ended and its record unchanged.
func (f *fakeStore) ApplyReview(_ context.Context, orgID, docID string, recordUpdatedAt time.Time,
	rv db.RecordReview, ev *db.AuditEvent) (time.Time, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return time.Time{}, f.failWith
	}
	d := f.documentIn(orgID, docID)
	ins := f.insights[docID]
	rec := f.records[docID]
	if d == nil || ins == nil || rec == nil ||
		(ins.Status != "checks_passed" && ins.Status != "verified" && ins.Status != "needs_review") ||
		!rec.UpdatedAt.Equal(recordUpdatedAt) {
		return time.Time{}, db.ErrReviewConflict
	}
	if f.failAudit != nil {
		return time.Time{}, f.failAudit
	}
	rec.Record = rv.Record
	rec.UpdatedAt = rv.At
	ins.HTMLInsights = rv.HTML
	if rv.Verified && ins.Status == "needs_review" {
		ins.Status = "verified"
	}
	f.reviews = append(f.reviews, rv)
	f.logAudit(ev)
	return rv.At, nil
}

// addDocument stores a document of the patient directly, bypassing the
// upload route, as uploaded by the account.
func (f *fakeStore) addDocument(p *db.Patient, uploadedBy, storagePath string) string {
	d, _ := f.CreateDocument(context.Background(), p.OrgID, db.NewDocument{PatientID: p.ID, UploadedBy: uploadedBy,
		OriginalName: "report.pdf", ContentType: "application/pdf", StoragePath: storagePath}, nil)
	return d.ID
}

// aiCall is one request the fake AI service received.
type aiCall struct {
	Path  string
	Token string
	Body  map[string]string // the body, when it is flat
	Raw   []byte
}

// fakeAI stands in for the Python AI service.
type fakeAI struct {
	mu     sync.Mutex
	calls  []aiCall
	status int // response status; 200 unless set
	// respond, when set, answers a route that returns JSON (review-apply):
	// the status and body for a request's path and raw body; status 0
	// falls through to the plain answer.
	respond func(path string, raw []byte) (int, []byte)
	srv     *httptest.Server
}

func newFakeAI(t *testing.T) *fakeAI {
	t.Helper()
	ai := &fakeAI{status: http.StatusOK}
	ai.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var body map[string]string
		_ = json.Unmarshal(raw, &body)
		ai.mu.Lock()
		ai.calls = append(ai.calls, aiCall{Path: r.URL.Path, Token: r.Header.Get("X-Internal-Token"), Body: body, Raw: raw})
		status, respond := ai.status, ai.respond
		ai.mu.Unlock()
		if respond != nil {
			if code, out := respond(r.URL.Path, raw); code != 0 {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(code)
				_, _ = w.Write(out)
				return
			}
		}
		w.WriteHeader(status)
	}))
	t.Cleanup(ai.srv.Close)
	return ai
}

func (ai *fakeAI) received() []aiCall {
	ai.mu.Lock()
	defer ai.mu.Unlock()
	return append([]aiCall(nil), ai.calls...)
}

// testEnv is a Server wired to a fake store, a fake AI service, a
// temporary uploads directory and a clock the test moves.
type testEnv struct {
	t       *testing.T
	server  *Server
	store   *fakeStore
	ai      *fakeAI
	uploads string
	patient *db.Patient // the default patient of uploadPath

	mu    sync.Mutex
	clock time.Time
}

// uploadPath is the upload route of a patient in the test organisation,
// created on first use.
func (e *testEnv) uploadPath() string {
	if e.patient == nil {
		e.patient = e.store.addPatient("org-1", "SH-UPLOAD")
	}
	return "/patients/" + e.patient.ID + "/documents"
}

func newTestEnv(t *testing.T) *testEnv {
	t.Helper()
	store := newFakeStore()
	ai := newFakeAI(t)
	uploads := t.TempDir()
	e := &testEnv{t: t, store: store, ai: ai, uploads: uploads,
		clock: time.Date(2026, 11, 8, 9, 0, 0, 0, time.UTC)}
	e.server = NewServer(store, Config{
		UploadsDir:     uploads,
		InsightsURL:    ai.srv.URL + "/",
		InternalToken:  testInternalToken,
		AllowedOrigins: []string{testOrigin},
		CookieSecure:   true,
		AppURL:         testAppURL + "/",
		Hasher:         testHasher,
		Now:            e.now,
	})
	return e
}

func (e *testEnv) now() time.Time {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.clock
}

func (e *testEnv) advance(d time.Duration) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.clock = e.clock.Add(d)
}

// login creates an active account with testPassword, logs it in through
// POST /auth/login and returns its ID and session cookie value.
func (e *testEnv) login(email string) (userID, session string) {
	e.t.Helper()
	u := e.store.addUser(email, db.StatusActive)
	return u.ID, e.loginAs(email, testPassword)
}

// loginWith is login with roles.
func (e *testEnv) loginWith(email string, roles ...string) (userID, session string) {
	e.t.Helper()
	u := e.store.addUser(email, db.StatusActive, roles...)
	return u.ID, e.loginAs(email, testPassword)
}

// loginAs logs in through the API and returns the session cookie value.
func (e *testEnv) loginAs(email, password string) string {
	e.t.Helper()
	body, _ := json.Marshal(map[string]string{"email": email, "password": password})
	rec := e.do(http.MethodPost, "/auth/login", "", bytes.NewReader(body), "application/json")
	if rec.Code != http.StatusOK {
		e.t.Fatalf("login as %s: status %d: %s", email, rec.Code, rec.Body.String())
	}
	c := sessionCookieFrom(e.t, rec)
	if c == nil {
		e.t.Fatal("login set no session cookie")
	}
	return c.Value
}

// sessionCookieFrom returns the session cookie a response set, or nil.
func sessionCookieFrom(t *testing.T, rec *httptest.ResponseRecorder) *http.Cookie {
	t.Helper()
	for _, c := range rec.Result().Cookies() {
		if c.Name == sessionCookie {
			return c
		}
	}
	return nil
}

// do sends a request through the server, as the frontend would: with the
// session cookie when session is set, and the CSRF header on requests that
// change something.
func (e *testEnv) do(method, path, session string, body io.Reader, contentType string) *httptest.ResponseRecorder {
	e.t.Helper()
	req := httptest.NewRequest(method, path, body)
	if session != "" {
		req.AddCookie(&http.Cookie{Name: sessionCookie, Value: session})
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	if !safeMethod(method) {
		req.Header.Set(csrfHeader, csrfValue)
	}
	rec := httptest.NewRecorder()
	e.server.ServeHTTP(rec, req)
	return rec
}

// postJSON sends a JSON body with do.
func (e *testEnv) postJSON(path, session string, v any) *httptest.ResponseRecorder {
	e.t.Helper()
	body, err := json.Marshal(v)
	if err != nil {
		e.t.Fatal(err)
	}
	return e.do(http.MethodPost, path, session, bytes.NewReader(body), "application/json")
}

// sendJSON sends a JSON body with any method.
func (e *testEnv) sendJSON(method, path, session string, v any) *httptest.ResponseRecorder {
	e.t.Helper()
	body, err := json.Marshal(v)
	if err != nil {
		e.t.Fatal(err)
	}
	return e.do(method, path, session, bytes.NewReader(body), "application/json")
}

// multipartFile builds an upload body with one file field.
func multipartFile(t *testing.T, field, name string, content []byte) (*bytes.Buffer, string) {
	t.Helper()
	var buf bytes.Buffer
	w := multipart.NewWriter(&buf)
	part, err := w.CreateFormFile(field, name)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := part.Write(content); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return &buf, w.FormDataContentType()
}

func decodeJSON[T any](t *testing.T, rec *httptest.ResponseRecorder) T {
	t.Helper()
	var v T
	if err := json.Unmarshal(rec.Body.Bytes(), &v); err != nil {
		t.Fatalf("response is not JSON: %v\n%s", err, rec.Body.String())
	}
	return v
}

// Smallest valid file headers for each accepted type.
var (
	pdfBytes  = []byte("%PDF-1.7\n1 0 obj\n<<>>\nendobj\n")
	pngBytes  = []byte("\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00")
	jpegBytes = []byte("\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
)

// listsPatient reports whether an event's details list the patient among
// patient_ids, as ListAuditEvents's patient filter matches them.
func listsPatient(details map[string]any, patientID string) bool {
	switch ids := details["patient_ids"].(type) {
	case []string:
		for _, id := range ids {
			if id == patientID {
				return true
			}
		}
	}
	return false
}
