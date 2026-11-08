package authz

import (
	"strings"
	"testing"
)

var all = []Permission{
	PatientRead, PatientWrite, DocumentUpload, DocumentRead, DocumentDelete, DocumentReprocess,
	ReviewWrite, SummaryRead, SummaryGenerate, StaffManage, AuditRead,
}

// The permission matrix, written out in full so a
// change to rolePermissions has to change this test too.
func TestPermissionMatrix(t *testing.T) {
	want := map[Permission][2]bool{ // clinician, admin
		PatientRead:       {true, false},
		PatientWrite:      {true, false},
		DocumentUpload:    {true, false},
		DocumentRead:      {true, false},
		DocumentDelete:    {true, false},
		DocumentReprocess: {true, false},
		ReviewWrite:       {true, false},
		SummaryRead:       {true, false},
		SummaryGenerate:   {true, false},
		StaffManage:       {false, true},
		AuditRead:         {false, true},
	}
	if len(want) != len(all) {
		t.Fatalf("the matrix covers %d permissions, want %d", len(want), len(all))
	}
	for _, p := range all {
		if got := Can([]string{RoleClinician}, p); got != want[p][0] {
			t.Errorf("clinician %s = %v, want %v", p, got, want[p][0])
		}
		if got := Can([]string{RoleAdmin}, p); got != want[p][1] {
			t.Errorf("admin %s = %v, want %v", p, got, want[p][1])
		}
		if !Can([]string{RoleAdmin, RoleClinician}, p) {
			t.Errorf("admin + clinician lacks %s", p)
		}
	}
}

func TestNoRoleOrUnknownRoleGrantsNothing(t *testing.T) {
	for _, roles := range [][]string{nil, {}, {"superuser"}, {"Admin"}, {""}} {
		for _, p := range all {
			if Can(roles, p) {
				t.Errorf("roles %v grant %s", roles, p)
			}
		}
		if len(Permissions(roles)) != 0 {
			t.Errorf("Permissions(%v) is not empty", roles)
		}
	}
}

func TestPermissionsAreSortedAndUnique(t *testing.T) {
	got := Permissions([]string{RoleClinician, RoleAdmin, RoleClinician})
	if len(got) != len(all) {
		t.Fatalf("got %d permissions, want %d", len(got), len(all))
	}
	for i := 1; i < len(got); i++ {
		if strings.Compare(string(got[i-1]), string(got[i])) >= 0 {
			t.Errorf("not sorted and unique at %d: %v", i, got)
		}
	}
}

func TestKnownRole(t *testing.T) {
	if !KnownRole(RoleClinician) || !KnownRole(RoleAdmin) || KnownRole("doctor") {
		t.Error("KnownRole is wrong")
	}
}
