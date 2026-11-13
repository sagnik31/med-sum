# 0010. Staff access: admin-issued accounts, server-side sessions, permissions in code, scope in SQL

- **Status:** Accepted, 2026-11-08; confirmed 2026-11-13, when a second factor was dropped from the scope. Built: accounts, passwords, sessions, CSRF protection, the permission layer and staff management. Not built yet: patient records with organisation scope in SQL, soft delete, the audit log, the per-IP login limit, the AI service's localhost bind and moving development secrets out of the run scripts.

## Context

The first version logged in with a phone number and an OTP field nothing checked, kept a 24-hour JWT in `localStorage`, and authorized every query as `WHERE user_id = caller`. Constraints: local only, no paid services (no SMS, no hosted identity provider, no email), one Go API in front of Postgres, and a repository where the security reasoning should be readable.

## Decision

**Accounts.** Email and password; **no self sign-up**. An admin creates an account (`medsumctl` for an organisation's first admin, the Staff page after that) and gets a **one-time setup link** (72 hours) to hand over; the person sets their own password, so the admin never knows it. A reset is the same kind of link (24 hours), since there is no email. The token sits in the URL fragment, which browsers don't send to servers, and only its SHA-256 is stored. Passwords are hashed with argon2id (OWASP parameters, PHC format so they can be raised), need 12 to 128 characters with no composition rules, and are refused if common, a short repeated pattern, or containing the email's local part. One generic "invalid email or password"; a dummy hash check for an unknown email, so timing doesn't reveal accounts; 15 minutes' lockout after 5 failures; an in-memory limit of 10 attempts a minute per IP on `/auth/*`. No default credentials exist; `seed-dev` refuses outside `MEDSUM_ENV=dev`.

**Sessions** are rows in `sessions`, not tokens the browser can read. The cookie `medsum_session` (`HttpOnly`, `SameSite=Strict`, `Secure` outside local HTTP) holds 32 random bytes; the database holds only their hash. 30 minutes idle, 12 hours absolute (one shift). A new session at every login; changing a password, disabling an account or changing roles ends the other sessions; logout deletes the row. Users can list and end their own sessions.

**CSRF.** Every state-changing request must carry `X-Requested-With: med-sum`, and its `Origin`, when present, must be in `ALLOWED_ORIGINS` (never `*` with credentials). A cross-site form can't set the header, a cross-site `fetch` with it needs a preflight CORS refuses, and a request without `Origin` isn't from another site's page. With `SameSite=Strict`, no CSRF token is needed. In development Vite proxies `/api`, so the cookie is first-party.

**Authorization in two layers.** (1) Middleware loads the principal from the session (401 without one), and handlers ask for a **permission, never a role** (403 without it); `authz.Can` is a pure function tested over every role and permission. (2) Every store method that touches patient data takes the caller's organisation and filters on it in SQL, so a handler can't fetch an unscoped row; ownership rules are checked against the loaded row.

| Permission | clinician | admin | Rule |
|---|:-:|:-:|---|
| `patient.read`, `patient.write`, `document.upload`, `document.read`, `document.reprocess`, `summary.generate`, `summary.read` | ✓ | | same organisation |
| `review.write` | ✓ | | same organisation; saved with who and when |
| `document.delete` | ✓ | | only the uploader; soft delete; audited |
| `staff.manage` | | ✓ | same organisation; can't drop their own admin role or disable themselves; one active admin always kept |
| `audit.read` | | ✓ | same organisation |
| own account and sessions | ✓ | ✓ | |

**Admins see no clinical data.** Another organisation's resource answers **404, not 403**, so ids can't be probed.

**Audit log.** `audit_events` (time, organisation, actor, action, target, patient, outcome, IP, user agent, small details; **never clinical content**), append-only by a trigger that rejects UPDATE and DELETE. One row per login success or failure, lockout, logout, staff change, and **every read and write of patient data**. Mutations write it in their transaction; reads write it before returning data and **fail closed** (500).

**The browser never calls the AI service.** Only the Go API does, after its checks, with a shared secret in `X-Internal-Token` compared in constant time; the AI service takes ids, never paths or content from the browser, and is to listen on localhost only.

## Alternatives rejected

- **Phone and OTP; a hosted identity provider; TOTP.** SMS costs money and the unchecked OTP was no factor; a hosted provider is a paid or cloud dependency; a second factor was designed and dropped from the scope.
- **A JWT, in `localStorage` or a cookie.** Readable by script or not revocable; with one API and a database, statelessness buys nothing.
- **A synchronizer CSRF token.** More state for protection the three layers already give.
- **A policy engine (Casbin, OPA); role checks or organisation checks in handlers.** Eleven permissions don't need an engine; one forgotten handler check would leak another hospital's data.
- **Hard delete; application logs as the audit trail; the frontend calling the AI service.** A deleted upload must still be accountable; logs are mutable; every rule would have to be repeated in Python.

## Consequences

- Covered: credential stuffing and guessing, account enumeration, token theft by script, CSRF, session fixation, stale access after someone leaves, access across hospitals, privilege escalation, stolen database rows (hashed passwords and tokens), and untraceable access once the audit log exists.
- Open, and stated in the README: licences not verified; links handed over in person; one organisation; no per-patient restrictions or break-glass access; no encryption at rest for uploads; TLS is the deployer's job.
- API clients must send the CSRF header on non-GET requests; an audit failure blocks access rather than letting it through.

## What would change it

A real deployment: the hospital's identity provider, email for links, a second-factor policy, departments needing finer scopes, break-glass rules, or services on separate hosts needing TLS between them.
