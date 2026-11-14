package httpapi

import (
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

const actDocumentFileViewed = "document_file_viewed"

// ----- The original upload: GET /documents/{id}/file -----

// handleGetDocumentFile serves a document's stored file, so a clinician
// can check values against the original: images show directly, PDFs in
// the browser's own viewer (the app opens it at the cited page with
// #page=N). The type comes from the stored file's extension, which the
// upload checked against the file's first bytes, never from what the
// uploading browser said. The viewing is audited before a byte is sent.
// See docs/adr/0011-clinician-review.md.
func (s *Server) handleGetDocumentFile(w http.ResponseWriter, r *http.Request, docID string) {
	doc := s.loadDocument(w, r, docID)
	if doc == nil {
		return
	}
	ext := strings.ToLower(filepath.Ext(doc.StoragePath))
	contentType, ok := allowedTypes[ext]
	if !ok || !insideDir(s.uploadsDir, doc.StoragePath) {
		// Uploads are stored only under the uploads directory with one of
		// these extensions; anything else is a row not made by an upload.
		log.Printf("document %s has no servable file", docID)
		http.Error(w, "this document has no file to show", http.StatusNotFound)
		return
	}
	f, err := os.Open(doc.StoragePath)
	if err != nil {
		log.Printf("document %s: file not readable: %v", docID, err)
		http.Error(w, "this document's file is missing", http.StatusNotFound)
		return
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || info.IsDir() {
		http.Error(w, "this document's file is missing", http.StatusNotFound)
		return
	}
	if !s.recordAudit(w, r, s.documentEvent(r, actDocumentFileViewed, doc)) {
		return
	}

	h := w.Header()
	h.Set("Content-Type", contentType)
	// A generic name: the uploaded name may carry the patient's name.
	h.Set("Content-Disposition", `inline; filename="document`+ext+`"`)
	// The one route whose answer the app frames. Every other response is
	// unframeable and runs nothing (withSecurityHeaders); here the app's
	// own origin may frame it, and the browser's PDF viewer may draw it,
	// but it still loads nothing from elsewhere and runs no script of its
	// own. X-Frame-Options can't name an origin, so it is dropped for the
	// CSP's frame-ancestors.
	h.Set("Content-Security-Policy", s.fileCSP())
	h.Del("X-Frame-Options")
	http.ServeContent(w, r, "document"+ext, info.ModTime(), f)
}

// fileCSP is the policy for a served upload: framed only by the app.
func (s *Server) fileCSP() string {
	ancestors := []string{"'self'"}
	for o := range s.allowedOrigins {
		ancestors = append(ancestors, o)
	}
	sort.Strings(ancestors[1:])
	return "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; object-src 'self'; " +
		"frame-ancestors " + strings.Join(ancestors, " ")
}

// insideDir reports whether path is inside dir (after cleaning both).
func insideDir(dir, path string) bool {
	if dir == "" {
		return false
	}
	rel, err := filepath.Rel(filepath.Clean(dir), filepath.Clean(path))
	return err == nil && rel != "." && !strings.HasPrefix(rel, "..") && !filepath.IsAbs(rel)
}
