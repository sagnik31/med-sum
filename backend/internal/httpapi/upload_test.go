package httpapi

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"backend/internal/db"
)

// memFile is an in-memory multipart.File.
type memFile struct{ *bytes.Reader }

func (memFile) Close() error { return nil }

func TestCheckFileType(t *testing.T) {
	tests := []struct {
		name    string
		file    string
		content []byte
		wantErr string // "" when accepted
	}{
		{"pdf", "report.pdf", pdfBytes, ""},
		{"png", "scan.png", pngBytes, ""},
		{"jpg", "scan.jpg", jpegBytes, ""},
		{"jpeg", "scan.jpeg", jpegBytes, ""},
		{"extension in upper case", "SCAN.PDF", pdfBytes, ""},
		{"unsupported extension", "scan.gif", []byte("GIF89a"), "unsupported file type"},
		{"no extension", "report", pdfBytes, "unsupported file type"},
		{"executable", "report.exe", []byte("MZ\x90\x00"), "unsupported file type"},
		{"png named pdf", "report.pdf", pngBytes, "does not match"},
		{"html named pdf", "report.pdf", []byte("<html><script>x</script></html>"), "does not match"},
		{"empty file", "report.pdf", nil, "could not read"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			f := memFile{bytes.NewReader(tt.content)}

			err := checkFileType(f, tt.file)

			if tt.wantErr == "" {
				if err != nil {
					t.Fatalf("rejected: %v", err)
				}
				rest, _ := io.ReadAll(f)
				if !bytes.Equal(rest, tt.content) {
					t.Error("file was not rewound after the check")
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
				t.Errorf("err = %v, want one containing %q", err, tt.wantErr)
			}
		})
	}
}

func TestNewStoragePathIsRandomAndKeepsTheExtension(t *testing.T) {
	dir := t.TempDir()
	name := regexp.MustCompile(`^[0-9a-f]{32}\.pdf$`)

	first, err := newStoragePath(dir, "Patient Report.PDF")
	if err != nil {
		t.Fatal(err)
	}
	second, _ := newStoragePath(dir, "Patient Report.PDF")

	if filepath.Dir(first) != dir {
		t.Errorf("path %q is not in %q", first, dir)
	}
	if !name.MatchString(filepath.Base(first)) {
		t.Errorf("name %q, want 32 hex characters and .pdf", filepath.Base(first))
	}
	if first == second {
		t.Error("two uploads got the same path")
	}
}

func TestUploadSavesTheFileAndStartsProcessing(t *testing.T) {
	e := newTestEnv(t)
	userID, auth := e.login("me@hospital.test")
	body, contentType := multipartFile(t, "file", "cbc.pdf", pdfBytes)

	rec := e.do(http.MethodPost, "/documents", auth, body, contentType)

	if rec.Code != http.StatusCreated {
		t.Fatalf("status = %d, want 201: %s", rec.Code, rec.Body)
	}
	doc := decodeJSON[db.Document](t, rec)
	if doc.UserID != userID || doc.OriginalName != "cbc.pdf" {
		t.Errorf("document = %+v", doc)
	}
	if strings.Contains(rec.Body.String(), e.uploads) {
		t.Error("response leaks the server-side storage path")
	}

	stored := e.store.documents[doc.ID]
	if filepath.Dir(stored.StoragePath) != e.uploads || !filepath.IsAbs(stored.StoragePath) {
		t.Errorf("stored at %q, want an absolute path in the uploads dir", stored.StoragePath)
	}
	saved, err := os.ReadFile(stored.StoragePath)
	if err != nil || !bytes.Equal(saved, pdfBytes) {
		t.Errorf("saved file = %q, %v", saved, err)
	}

	calls := e.ai.received()
	if len(calls) != 1 {
		t.Fatalf("AI service got %d calls, want 1", len(calls))
	}
	want := aiCall{Path: "/internal/generate-insights", Token: testInternalToken,
		Body: map[string]string{"document_id": doc.ID}}
	if calls[0].Path != want.Path || calls[0].Token != want.Token || calls[0].Body["document_id"] != doc.ID {
		t.Errorf("AI call = %+v, want %+v", calls[0], want)
	}
}

func TestTheSameFileUploadedAgainIsFlaggedNotRefused(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	upload := func(name string, content []byte) (db.Document, string) {
		body, contentType := multipartFile(t, "file", name, content)
		rec := e.do(http.MethodPost, "/documents", auth, body, contentType)
		if rec.Code != http.StatusCreated {
			t.Fatalf("status = %d, want 201: %s", rec.Code, rec.Body)
		}
		return decodeJSON[db.Document](t, rec), rec.Body.String()
	}

	first, firstBody := upload("cbc.pdf", pdfBytes)
	again, _ := upload("cbc copy.pdf", pdfBytes)
	other, _ := upload("lft.pdf", append(append([]byte{}, pdfBytes...), ' '))

	if first.DuplicateOf != nil || strings.Contains(firstBody, "duplicate_of") {
		t.Errorf("first upload flagged: %s", firstBody)
	}
	if again.DuplicateOf == nil || *again.DuplicateOf != first.ID {
		t.Errorf("copy duplicate_of = %v, want %s", again.DuplicateOf, first.ID)
	}
	if other.DuplicateOf != nil {
		t.Errorf("a different file is flagged as a copy of %s", *other.DuplicateOf)
	}
	sum := sha256.Sum256(pdfBytes)
	if got := e.store.documents[first.ID].SHA256; got != hex.EncodeToString(sum[:]) {
		t.Errorf("stored hash = %q, want the file's SHA-256", got)
	}
	if strings.Contains(firstBody, hex.EncodeToString(sum[:])) {
		t.Error("the response carries the file hash")
	}
}

func TestUploadSucceedsWhenTheAIServiceIsDown(t *testing.T) {
	e := newTestEnv(t)
	e.ai.srv.Close()
	_, auth := e.login("me@hospital.test")
	body, contentType := multipartFile(t, "file", "rx.png", pngBytes)

	rec := e.do(http.MethodPost, "/documents", auth, body, contentType)

	if rec.Code != http.StatusCreated {
		t.Fatalf("status = %d, want 201", rec.Code)
	}
	if len(e.store.documents) != 1 {
		t.Error("document was not recorded")
	}
}

func TestRejectedUploadsStoreNothing(t *testing.T) {
	big := make([]byte, maxUploadSize+1)
	copy(big, pdfBytes)

	tests := []struct {
		name     string
		field    string
		file     string
		content  []byte
		wantCode int
	}{
		{"unsupported type", "file", "notes.txt", []byte("hello"), http.StatusUnsupportedMediaType},
		{"content does not match", "file", "report.pdf", pngBytes, http.StatusUnsupportedMediaType},
		{"too large", "file", "report.pdf", big, http.StatusRequestEntityTooLarge},
		{"wrong field name", "document", "report.pdf", pdfBytes, http.StatusBadRequest},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newTestEnv(t)
			_, auth := e.login("me@hospital.test")
			body, contentType := multipartFile(t, tt.field, tt.file, tt.content)

			rec := e.do(http.MethodPost, "/documents", auth, body, contentType)

			if rec.Code != tt.wantCode {
				t.Fatalf("status = %d, want %d: %s", rec.Code, tt.wantCode, rec.Body)
			}
			if entries, _ := os.ReadDir(e.uploads); len(entries) != 0 {
				t.Errorf("%d files left in uploads", len(entries))
			}
			if len(e.store.documents) != 0 || len(e.ai.received()) != 0 {
				t.Error("a rejected upload was recorded or processed")
			}
		})
	}
}

func TestUploadIsNotAMultipartForm(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")

	rec := e.do(http.MethodPost, "/documents", auth, strings.NewReader("{}"), "application/json")

	if rec.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", rec.Code)
	}
}

func TestUploadRemovesTheFileWhenTheDatabaseFails(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	e.store.failWith = io.ErrUnexpectedEOF
	body, contentType := multipartFile(t, "file", "cbc.pdf", pdfBytes)

	rec := e.do(http.MethodPost, "/documents", auth, body, contentType)

	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", rec.Code)
	}
	if entries, _ := os.ReadDir(e.uploads); len(entries) != 0 {
		t.Errorf("%d orphaned files left in uploads", len(entries))
	}
}
