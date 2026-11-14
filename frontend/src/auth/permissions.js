// src/auth/permissions.js
//
// The permissions the API grants (backend/internal/authz). The frontend
// uses them only to show or hide pages and buttons; the API enforces them.

export const PERM = {
  patientRead: "patient.read",
  patientWrite: "patient.write",
  documentRead: "document.read",
  documentDelete: "document.delete",
  documentUpload: "document.upload",
  reviewWrite: "review.write",
  summaryRead: "summary.read",
  summaryGenerate: "summary.generate",
  staffManage: "staff.manage",
  auditRead: "audit.read",
};

export function can(user, perm) {
  return Boolean(user?.permissions?.includes(perm));
}

// Where a logged-in user starts: clinicians at the patient list, admins at
// the staff page.
export function homePath(user) {
  if (can(user, PERM.patientRead)) return "/patients";
  if (can(user, PERM.staffManage)) return "/admin/staff";
  return "/account";
}
