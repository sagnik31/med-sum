# 0030. Authorization in two layers: permissions in code, organisation scope in SQL

- **Status:** Accepted, 2026-11-08; confirmed 2026-11-13. The permission layer, the admin rules and 404 across organisations for staff accounts are built. Organisation scope on patient data, patient permissions and soft delete come with patient records (see [0002](0002-hospital-staff-are-the-users.md)).

## Context

Every query used to be `WHERE user_id = caller`: no roles, no organisation, no idea of a clinician seeing a patient. Authorization bugs in healthcare apps are mostly a handler that forgot a check.

## Decision

1. **Route layer.** Middleware loads the principal (user id, organisation, roles, name) from the session or answers 401; `require(permission)` answers 403. Handlers ask for a **permission, never a role**, so adding a role is one map entry in `internal/authz`. `authz.Can(principal, permission, resource)` is a pure function, tested as a table over every role and permission.
2. **Data layer.** Every store method that touches patient data takes the caller's organisation id and filters on it in SQL. A handler can't fetch a row without an organisation scope, because no such store method exists. Ownership rules are checked in `authz` against the loaded row.

**Permissions:**

| Permission | clinician | admin | Rule |
|---|:-:|:-:|---|
| `patient.read`, `patient.write` | ✓ | | same organisation |
| `document.upload`, `document.read` (view, record, citations, source file) | ✓ | | same organisation |
| `document.delete` | ✓ | | **only the uploader**; soft delete; audited |
| `document.reprocess`, `review.write`, `summary.generate`, `summary.read` | ✓ | | same organisation; reviews saved with who and when |
| `staff.manage` (invite, disable, roles, reset link) | | ✓ | same organisation; can't drop their own admin role or disable themselves; an organisation keeps at least one active admin |
| `audit.read` | | ✓ | same organisation |
| own account (view, password, log out, sessions) | ✓ | ✓ | |

- **Admins see no clinical data** (least privilege). Someone who needs both holds both roles.
- **Another organisation's resource answers 404, not 403**, so ids from elsewhere can't be probed. A missing permission answers 403.
- The frontend shows only the pages an account may use, but the API is what enforces it.

## Alternatives rejected

- **A policy engine (Casbin, OPA).** Eleven permissions don't need one, and plain Go is easier to read and review.
- **Role checks in handlers.** Every new role would touch every handler.
- **Organisation checks in handlers only.** One forgotten check leaks another hospital's data; SQL scoping makes the forgotten check impossible.
- **Hard delete of documents.** A wrong upload must be removable, but the record of it must stay for the audit.

## Consequences

- Covered: horizontal access across organisations (scope in SQL, 404) and privilege escalation (permissions in code, admins kept from clinical data, the last-admin rule).
- Open, and stated in the README: a single organisation; no per-patient restrictions or break-glass access.

## What would change it

Departments or care teams that need finer scopes than an organisation, or regulations requiring break-glass access with its own audit.
