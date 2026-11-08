// src/auth/permissions.js
//
// The permissions the API grants (backend/internal/authz). The frontend
// uses them only to show or hide pages and buttons; the API enforces them.

export const PERM = {
  documentRead: "document.read",
  documentUpload: "document.upload",
  staffManage: "staff.manage",
};

export function can(user, perm) {
  return Boolean(user?.permissions?.includes(perm));
}

// Where a logged-in user starts: clinicians at their documents, admins at
// the staff page.
export function homePath(user) {
  if (can(user, PERM.documentRead)) return "/documents";
  if (can(user, PERM.staffManage)) return "/admin/staff";
  return "/account";
}
