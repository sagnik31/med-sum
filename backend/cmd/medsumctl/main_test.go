package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"backend/internal/auth"
)

func TestParseRoles(t *testing.T) {
	good := map[string]string{
		"clinician":              "clinician",
		"admin, clinician":       "admin clinician",
		"clinician,clinician,  ": "clinician",
	}
	for in, want := range good {
		got, err := parseRoles(in)
		if err != nil || strings.Join(got, " ") != want {
			t.Errorf("parseRoles(%q) = %v, %v; want %s", in, got, err, want)
		}
	}
	for _, in := range []string{"", " , ", "doctor", "admin,root"} {
		if _, err := parseRoles(in); err == nil {
			t.Errorf("parseRoles(%q) accepted", in)
		}
	}
}

// The dataset loader (scripts/src/dataset/load.py) logs in with these
// addresses; they must be valid account emails.
func TestDevAccountEmailsAreValid(t *testing.T) {
	for _, email := range []string{devAdminEmail, devClinicianEmail} {
		if got, err := auth.NormalizeEmail(email); err != nil || got != email {
			t.Errorf("%s: NormalizeEmail = %q, %v", email, got, err)
		}
	}
}

func TestOrphansAreTheFilesNoDocumentNames(t *testing.T) {
	dir := t.TempDir()
	for _, name := range []string{"a.pdf", "b.pdf", "c.png"} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	got, err := orphans(dir, []string{filepath.Join(dir, "a.pdf"), filepath.Join(dir, "c.png"), "/elsewhere/z.pdf"})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || filepath.Base(got[0]) != "b.pdf" {
		t.Errorf("orphans = %v", got)
	}
	if got, err := orphans(filepath.Join(dir, "missing"), nil); err != nil || len(got) != 0 {
		t.Errorf("a missing directory = %v, %v", got, err)
	}
}
