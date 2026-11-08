// Package authz decides what an account may do. Handlers ask for a permission, never for a role, so a new
// role is one entry in rolePermissions.
package authz

import "sort"

// Permission is one thing an account may be allowed to do.
type Permission string

const (
	PatientRead       Permission = "patient.read"
	PatientWrite      Permission = "patient.write"
	DocumentUpload    Permission = "document.upload"
	DocumentRead      Permission = "document.read"
	DocumentDelete    Permission = "document.delete"
	DocumentReprocess Permission = "document.reprocess"
	ReviewWrite       Permission = "review.write"
	SummaryRead       Permission = "summary.read"
	SummaryGenerate   Permission = "summary.generate"
	StaffManage       Permission = "staff.manage"
	AuditRead         Permission = "audit.read"
)

// Roles.
const (
	RoleClinician = "clinician"
	RoleAdmin     = "admin"
)

// rolePermissions is the permission matrix. Admins manage staff and read
// the audit log but see no clinical data (least privilege); a person who
// needs both holds both roles.
var rolePermissions = map[string][]Permission{
	RoleClinician: {
		PatientRead, PatientWrite,
		DocumentUpload, DocumentRead, DocumentDelete, DocumentReprocess,
		ReviewWrite, SummaryRead, SummaryGenerate,
	},
	RoleAdmin: {StaffManage, AuditRead},
}

// KnownRole reports whether role is one the app has.
func KnownRole(role string) bool {
	_, ok := rolePermissions[role]
	return ok
}

// Can reports whether an account with these roles has the permission.
// Unknown roles grant nothing.
func Can(roles []string, p Permission) bool {
	for _, r := range roles {
		for _, granted := range rolePermissions[r] {
			if granted == p {
				return true
			}
		}
	}
	return false
}

// Permissions lists everything the roles grant, sorted, for the UI.
func Permissions(roles []string) []Permission {
	seen := map[Permission]bool{}
	out := []Permission{}
	for _, r := range roles {
		for _, p := range rolePermissions[r] {
			if !seen[p] {
				seen[p] = true
				out = append(out, p)
			}
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i] < out[j] })
	return out
}
