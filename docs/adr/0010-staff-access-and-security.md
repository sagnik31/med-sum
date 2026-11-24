# 0010. Staff access: admin-issued accounts, server-side sessions, permissions in code, scope in SQL

- **Status:** Accepted, 2026-11-08; confirmed 2026-11-13, when a second factor was dropped from the scope. Built: accounts, passwords, sessions, CSRF protection, the permission layer, staff management, patient records with organisation scope in SQL, soft delete (by the uploader only; the patient summary is cleared in the same transaction, by a trigger, and rebuilt without the document), and the audit log. Built since: the per-address login limit, security headers, the idle warning, secrets in untracked `.env.local` files with start-up refusing a missing or short internal token, the AI service's localhost bind, and the API's own database role (see [0008](0008-patient-data-protection.md)).

## Context

The first version logged in with a phone number and an OTP field nothing checked, kept a 24-hour JWT in `localStorage`, and authorized every query as `WHERE user_id = caller`. Constraints: local only, no paid services (no SMS, no hosted identity provider, no email), one Go API in front of Postgres, and a repository where the security reasoning should be readable.

## Decision

**Accounts.** Email and password; **no self sign-up**. An admin creates an account (`medsumctl` for an organisation's first admin, the Staff page after that) and gets a **one-time setup link** (72 hours) to hand over; the person sets their own password, so the admin never knows it. A reset is the same kind of link (24 hours), since there is no email. The token sits in the URL fragment, which browsers don't send to servers, and only its SHA-256 is stored. Passwords are hashed with argon2id (OWASP parameters, PHC format so they can be raised), need 12 to 128 characters with no composition rules, and are refused if common, a short repeated pattern, or containing the email's local part. One generic "invalid email or password"; a dummy hash check for an unknown email, so timing doesn't reveal accounts; 15 minutes' lockout after 5 failures; an in-memory limit of 10 attempts a minute per IP on the `/auth/*` routes that check a password or a link token (not `/auth/me`, which every page load calls). The IP is the request's own unless it comes from a proxy listed in `TRUSTED_PROXIES`, whose `X-Forwarded-For` names it (the last hop that isn't a trusted proxy); a client's own header is never believed, and without the setting every user behind a proxy would share one limit. No default credentials exist; `seed-dev` refuses outside `MEDSUM_ENV=dev`.

**Sessions** are rows in `sessions`, not tokens the browser can read. The cookie `medsum_session` (`HttpOnly`, `SameSite=Strict`, `Secure` outside local HTTP) holds 32 random bytes; the database holds only their hash. 30 minutes idle, 12 hours absolute (one shift). A new session at every login; changing a password, disabling an account or changing roles ends the other sessions; logout deletes the row. A login creates its session, and a password change ends the others, in one transaction that requires the stored hash to still be the one the password was checked against, so a login or change that a reset overtakes does nothing (2026-11-24). Users can list and end their own sessions. A poll the page makes on its own (a document or a summary being processed) sends `X-Medsum-Background: 1`: its session is checked as usual, but it isn't activity and doesn't slide the idle timeout, so a screen left open on a long job still logs out. The page's own check of the session, when its time is up or its tab comes back, is sent the same way. A logout hides every page at once and is shown as done only when the API has ended the session; a failed one says so and offers to try again. A login or logout in one tab reaches the browser's other tabs, which share the cookie (2026-11-24).

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

**Audit log.** `audit_events` (time, organisation, actor, action, target, patient, outcome, IP, user agent, small details; **never clinical content**), append-only by a trigger that rejects UPDATE and DELETE. One row per login success or failure, lockout, logout, staff change, and **every read and write of patient data**. Mutations write it in their transaction; reads write it before returning data and **fail closed** (500). As built: the columns carry no foreign keys, so purging a patient or an account keeps their trail; a refused permission and a refused delete are rows too (outcome `denied`; the row keeps the routes' own words and ids of the path and stores any other segment as `unknown`, since a client can send any text there); a search keeps only whether there was one and the count; a failed login with an unknown email has no organisation, so it is in the table but on no admin's page; and the trigger doesn't stop `TRUNCATE` by the schema's owner, which the API's own role can't do. A listing that shows patients' names without a search (the review queue) keeps their ids in `details.patient_ids`, and the log's patient filter matches those as well as the event's own patient.

**The browser never calls the AI service.** Only the Go API does, after its checks, with a shared secret in `X-Internal-Token` compared in constant time; the AI service takes ids, never paths or content from the browser, and is to listen on localhost only.

## Alternatives rejected

- **Phone and OTP; a hosted identity provider; TOTP.** SMS costs money and the unchecked OTP was no factor; a hosted provider is a paid or cloud dependency; a second factor was designed and dropped from the scope.
- **A JWT, in `localStorage` or a cookie.** Readable by script or not revocable; with one API and a database, statelessness buys nothing.
- **A synchronizer CSRF token.** More state for protection the three layers already give.
- **A policy engine (Casbin, OPA); role checks or organisation checks in handlers.** Eleven permissions don't need an engine; one forgotten handler check would leak another hospital's data.
- **Hard delete; application logs as the audit trail; the frontend calling the AI service.** A deleted upload must still be accountable; logs are mutable; every rule would have to be repeated in Python.

## Consequences

- Covered: credential stuffing and guessing, account enumeration, token theft by script, CSRF, session fixation, stale access after someone leaves, access across hospitals, privilege escalation, stolen database rows (hashed passwords and tokens), and untraceable access once the audit log exists.
- Open, and stated on the [limitations page](../limitations.md): licences not verified; links handed over in person; one organisation; no per-patient restrictions or break-glass access; no encryption at rest for uploads; TLS is the deployer's job.
- API clients must send the CSRF header on non-GET requests; an audit failure blocks access rather than letting it through.

## What would change it

A real deployment: the hospital's identity provider, email for links, a second-factor policy, departments needing finer scopes, break-glass rules, or services on separate hosts needing TLS between them.
