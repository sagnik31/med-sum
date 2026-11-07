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

	"backend/internal/db"
)

const (
	testJWTSecret     = "test-jwt-secret"
	testInternalToken = "test-internal-token"
)

// TestMain hides the handlers' error logs, which the tests trigger on
// purpose, unless the tests run with -v.
func TestMain(m *testing.M) {
	flag.Parse()
	if !testing.Verbose() {
		log.SetOutput(io.Discard)
	}
	os.Exit(m.Run())
}

// fakeStore is an in-memory Store. Documents are keyed by ID; insights by
// document ID; patient summaries by user ID.
type fakeStore struct {
	mu        sync.Mutex
	nextID    int
	users     map[string]*db.User // by phone number
	documents map[string]*db.Document
	insights  map[string]*db.Insight
	summaries map[string]*db.PatientSummary
	failWith  error // returned by every method when set
}

func newFakeStore() *fakeStore {
	return &fakeStore{
		users:     map[string]*db.User{},
		documents: map[string]*db.Document{},
		insights:  map[string]*db.Insight{},
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

func (f *fakeStore) UpsertUserByPhone(_ context.Context, phone string, fullName *string) (*db.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failWith != nil {
		return nil, f.failWith
	}
	u, ok := f.users[phone]
	if !ok {
		u = &db.User{ID: f.newID(), PhoneNumber: phone, CreatedAt: time.Now()}
		f.users[phone] = u
	}
	if fullName != nil {
		u.FullName = fullName
	}
	now := time.Now()
	u.LastLoginAt = &now
	copied := *u
	return &copied, nil
}

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

func (f *fakeStore) CreateDocument(_ context.Context, userID, originalName, contentType, storagePath string) (*db.Document, error) {
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
		Status:       "uploaded",
		UploadedAt:   now,
		UpdatedAt:    now,
	}
	f.documents[d.ID] = d
	copied := *d
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

// addDocument stores a document directly, bypassing the upload route.
func (f *fakeStore) addDocument(userID, storagePath string) string {
	d, _ := f.CreateDocument(context.Background(), userID, "report.pdf", "application/pdf", storagePath)
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

// testEnv is a Server wired to a fake store, a fake AI service and a
// temporary uploads directory.
type testEnv struct {
	t       *testing.T
	server  *Server
	store   *fakeStore
	ai      *fakeAI
	uploads string
}

func newTestEnv(t *testing.T) *testEnv {
	t.Helper()
	store := newFakeStore()
	ai := newFakeAI(t)
	uploads := t.TempDir()
	return &testEnv{
		t:       t,
		server:  NewServer(store, testJWTSecret, uploads, ai.srv.URL+"/", testInternalToken),
		store:   store,
		ai:      ai,
		uploads: uploads,
	}
}

// login creates a user through the fake store and returns its ID and an
// Authorization header value.
func (e *testEnv) login(phone string) (userID, auth string) {
	e.t.Helper()
	u, err := e.store.UpsertUserByPhone(context.Background(), phone, nil)
	if err != nil {
		e.t.Fatal(err)
	}
	token, err := e.server.generateToken(u.ID)
	if err != nil {
		e.t.Fatal(err)
	}
	return u.ID, "Bearer " + token
}

// do sends a request through the server and returns the recorded response.
func (e *testEnv) do(method, path, auth string, body io.Reader, contentType string) *httptest.ResponseRecorder {
	e.t.Helper()
	req := httptest.NewRequest(method, path, body)
	if auth != "" {
		req.Header.Set("Authorization", auth)
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	rec := httptest.NewRecorder()
	e.server.ServeHTTP(rec, req)
	return rec
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
