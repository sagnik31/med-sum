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
// patient summaries by user ID.
type fakeStore struct {
	mu        sync.Mutex
	nextID    int
	users     map[string]*db.User
	sessions  map[string]*db.Session
	tokens    map[string]*fakeToken
	documents map[string]*db.Document
	insights  map[string]*db.Insight
	records   map[string]*db.DocumentRecord
	summaries map[string]*db.PatientSummary
	failWith  error // returned by every method when set
}

type fakeToken struct {
	db.OneTimeToken
	UsedAt *time.Time
}

func newFakeStore() *fakeStore {
	return &fakeStore{
		users:     map[string]*db.User{},
		sessions:  map[string]*db.Session{},
		tokens:    map[string]*fakeToken{},
		documents: map[string]*db.Document{},
		insights:  map[string]*db.Insight{},
		records:   map[string]*db.DocumentRecord{},
		summaries: map[string]*db.PatientSummary{},
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

func (f *fakeStore) RecordLoginFailure(_ context.Context, userID string, maxFailures int, lockUntil time.Time) (*time.Time, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	u := f.users[userID]
	u.FailedLogins++
	if u.FailedLogins >= maxFailures {
		u.FailedLogins = 0
		u.LockedUntil = &lockUntil
	}
	return u.LockedUntil, nil
}

func (f *fakeStore) RecordLoginSuccess(_ context.Context, userID string, now time.Time) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return f.failWith
	}
	u := f.users[userID]
	u.FailedLogins, u.LockedUntil, u.LastLoginAt = 0, nil, &now
	return nil
}

func (f *fakeStore) SetPasswordHash(_ context.Context, userID, hash string, _ time.Time) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return f.failWith
	}
	f.users[userID].PasswordHash = &hash
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

func (f *fakeStore) DeleteSession(_ context.Context, tokenHash []byte) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return f.failWith
	}
	delete(f.sessions, string(tokenHash))
	return nil
}

func (f *fakeStore) DeleteUserSessions(_ context.Context, userID string, keep []byte) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return f.failWith
	}
	for k, sess := range f.sessions {
		if sess.UserID == userID && (keep == nil || k != string(keep)) {
			delete(f.sessions, k)
		}
	}
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

func (f *fakeStore) CompletePasswordSetup(_ context.Context, tokenHash []byte, passwordHash string, now time.Time) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return "", f.failWith
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
	return u.ID, nil
}

// ----- documents and summaries -----

func (f *fakeStore) GetPatientSummary(_ context.Context, userID string) (*db.PatientSummary, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	if s, ok := f.summaries[userID]; ok {
		copied := *s
		return &copied, nil
	}
	return &db.PatientSummary{}, nil
}

// duplicateOf is the first other document of d's user with d's file hash
// (IDs increase, so the smallest earlier ID); call it holding f.mu.
func (f *fakeStore) duplicateOf(d *db.Document) *string {
	var first *string
	for _, o := range f.documents {
		if o.UserID == d.UserID && o.SHA256 != "" && o.SHA256 == d.SHA256 && o.ID < d.ID &&
			(first == nil || o.ID < *first) {
			id := o.ID
			first = &id
		}
	}
	return first
}

func (f *fakeStore) CreateDocument(_ context.Context, userID, originalName, contentType, storagePath, sha256 string) (*db.Document, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	now := time.Now()
	d := &db.Document{
		ID:           f.newID(),
		UserID:       userID,
		OriginalName: originalName,
		ContentType:  contentType,
		StoragePath:  storagePath,
		UploadedAt:   now,
		UpdatedAt:    now,
		SHA256:       sha256,
	}
	f.documents[d.ID] = d
	copied := *d
	copied.DuplicateOf = f.duplicateOf(d)
	return &copied, nil
}

func (f *fakeStore) ListDocumentsByUser(_ context.Context, userID string) ([]db.Document, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	docs := []db.Document{}
	for _, d := range f.documents {
		if d.UserID == userID {
			copied := *d
			copied.DuplicateOf = f.duplicateOf(d)
			copied.InsightStatus = "not_started"
			if ins, ok := f.insights[d.ID]; ok {
				copied.InsightStatus = ins.Status
			}
			docs = append(docs, copied)
		}
	}
	return docs, nil
}

func (f *fakeStore) GetDocumentByIDAndUser(_ context.Context, docID, userID string) (*db.Document, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	d, ok := f.documents[docID]
	if !ok || d.UserID != userID {
		return nil, nil
	}
	copied := *d
	return &copied, nil
}

func (f *fakeStore) DeleteDocumentByIDAndUser(_ context.Context, docID, userID string) (string, bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return "", false, f.failWith
	}
	d, ok := f.documents[docID]
	if !ok || d.UserID != userID {
		return "", false, nil
	}
	delete(f.documents, docID)
	shared := false
	for _, other := range f.documents {
		if other.StoragePath == d.StoragePath {
			shared = true
		}
	}
	return d.StoragePath, shared, nil
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

// addDocument stores a document directly, bypassing the upload route.
func (f *fakeStore) addDocument(userID, storagePath string) string {
	d, _ := f.CreateDocument(context.Background(), userID, "report.pdf", "application/pdf", storagePath, "")
	return d.ID
}

// aiCall is one request the fake AI service received.
type aiCall struct {
	Path  string
	Token string
	Body  map[string]string
}

// fakeAI stands in for the Python AI service.
type fakeAI struct {
	mu     sync.Mutex
	calls  []aiCall
	status int // response status; 200 unless set
	srv    *httptest.Server
}

func newFakeAI(t *testing.T) *fakeAI {
	t.Helper()
	ai := &fakeAI{status: http.StatusOK}
	ai.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]string
		_ = json.NewDecoder(r.Body).Decode(&body)
		ai.mu.Lock()
		ai.calls = append(ai.calls, aiCall{Path: r.URL.Path, Token: r.Header.Get("X-Internal-Token"), Body: body})
		status := ai.status
		ai.mu.Unlock()
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

	mu    sync.Mutex
	clock time.Time
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
