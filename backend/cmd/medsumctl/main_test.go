package main

import (
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
	for _, email := range []string{devAdminEmail, devClinicianEmail, devPatientEmail("meera")} {
		if got, err := auth.NormalizeEmail(email); err != nil || got != email {
			t.Errorf("%s: NormalizeEmail = %q, %v", email, got, err)
		}
	}
	if got := devPatientEmail("meera"); got != "patient-meera@med-sum.test" {
		t.Errorf("devPatientEmail = %q", got)
	}
}
