package httpapi

import (
	"bytes"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// fileEnv is a clinician with one document whose file is in the uploads
// directory.
func fileEnv(t *testing.T, name string, content []byte) (*testEnv, string, string, string) {
	t.Helper()
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	path := filepath.Join(e.uploads, name)
	if err := os.WriteFile(path, content, 0o644); err != nil {
		t.Fatal(err)
	}
	doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, path)
	return e, me, auth, doc
}

func TestTheOriginalFileIsServedWithItsCheckedTypeAndAudited(t *testing.T) {
	e, _, auth, doc := fileEnv(t, "abc.pdf", pdfBytes)

	rec := e.do(http.MethodGet, "/documents/"+doc+"/file", auth, nil, "")
	if rec.Code != http.StatusOK || !bytes.Equal(rec.Body.Bytes(), pdfBytes) {
		t.Fatalf("status = %d, body = %q", rec.Code, rec.Body.String())
	}
	h := rec.Header()
	if h.Get("Content-Type") != "application/pdf" || h.Get("Content-Disposition") != `inline; filename="document.pdf"` {
		t.Errorf("headers = %v", h)
	}
	ev := e.store.audit[len(e.store.audit)-1]
	if ev.Action != actDocumentFileViewed || ev.TargetID == nil || *ev.TargetID != doc {
		t.Errorf("audit = %+v", ev)
	}
}

func TestOnlyTheFileRouteMayBeFramedAndOnlyByTheApp(t *testing.T) {
	e, _, auth, doc := fileEnv(t, "abc.png", pngBytes)

	rec := e.do(http.MethodGet, "/documents/"+doc+"/file", auth, nil, "")
	csp := rec.Header().Get("Content-Security-Policy")
	if !strings.Contains(csp, "frame-ancestors 'self' "+testOrigin) || !strings.HasPrefix(csp, "default-src 'none'") ||
		strings.Contains(csp, "script-src") {
		t.Errorf("CSP = %q", csp)
	}
	if rec.Header().Get("X-Frame-Options") != "" || rec.Header().Get("X-Content-Type-Options") != "nosniff" ||
		rec.Header().Get("Cache-Control") != "no-store" {
		t.Errorf("headers = %v", rec.Header())
	}
	if rec.Header().Get("Content-Type") != "image/png" {
		t.Errorf("type = %s", rec.Header().Get("Content-Type"))
	}

	// Every other document route keeps the strict policy.
	other := e.do(http.MethodGet, "/documents/"+doc, auth, nil, "")
	if other.Header().Get("Content-Security-Policy") != "default-src 'none'; frame-ancestors 'none'" ||
		other.Header().Get("X-Frame-Options") != "DENY" {
		t.Errorf("document route headers = %v", other.Header())
	}
}

func TestAnotherOrganisationsFileIsNotFoundAndKeepsTheStrictPolicy(t *testing.T) {
	e, me, auth, _ := fileEnv(t, "abc.pdf", pdfBytes)
	path := filepath.Join(e.uploads, "theirs.pdf")
	_ = os.WriteFile(path, pdfBytes, 0o644)
	theirs := e.store.addDocument(e.store.addPatient("org-2", "OT-1"), me, path)

	rec := e.do(http.MethodGet, "/documents/"+theirs+"/file", auth, nil, "")
	if rec.Code != http.StatusNotFound || bytes.Contains(rec.Body.Bytes(), pdfBytes) {
		t.Fatalf("status = %d", rec.Code)
	}
	if rec.Header().Get("X-Frame-Options") != "DENY" {
		t.Errorf("a refusal was frameable: %v", rec.Header())
	}
}

func TestTheFileIsRefusedWhenItCantBeAudited(t *testing.T) {
	e, _, auth, doc := fileEnv(t, "abc.pdf", pdfBytes)
	e.store.failAudit = errTest
	rec := e.do(http.MethodGet, "/documents/"+doc+"/file", auth, nil, "")
	if rec.Code != http.StatusInternalServerError || bytes.Contains(rec.Body.Bytes(), pdfBytes) {
		t.Fatalf("status = %d", rec.Code)
	}
}

func TestAFileOutsideTheUploadsDirectoryIsNeverServed(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	outside := filepath.Join(t.TempDir(), "secret.pdf")
	_ = os.WriteFile(outside, pdfBytes, 0o644)
	doc := e.store.addDocument(e.store.addPatient("org-1", "SH-1"), me, outside)
	if rec := e.do(http.MethodGet, "/documents/"+doc+"/file", auth, nil, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d", rec.Code)
	}
	txt := filepath.Join(e.uploads, "notes.txt")
	_ = os.WriteFile(txt, []byte("x"), 0o644)
	doc = e.store.addDocument(e.store.addPatient("org-1", "SH-2"), me, txt)
	if rec := e.do(http.MethodGet, "/documents/"+doc+"/file", auth, nil, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("txt status = %d", rec.Code)
	}
}

func TestAMissingFileIsNotFound(t *testing.T) {
	e, _, auth, doc := fileEnv(t, "abc.pdf", pdfBytes)
	_ = os.Remove(filepath.Join(e.uploads, "abc.pdf"))
	if rec := e.do(http.MethodGet, "/documents/"+doc+"/file", auth, nil, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d", rec.Code)
	}
}

func TestAnAdminCantSeeTheFile(t *testing.T) {
	e, _, _, doc := fileEnv(t, "abc.pdf", pdfBytes)
	_, admin := e.loginWith("admin@hospital.test", "admin")
	if rec := e.do(http.MethodGet, "/documents/"+doc+"/file", admin, nil, ""); rec.Code != http.StatusForbidden {
		t.Fatalf("status = %d", rec.Code)
	}
}
