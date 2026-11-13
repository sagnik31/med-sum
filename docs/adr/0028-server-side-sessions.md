# 0028. Server-side sessions in an HttpOnly cookie, not a JWT

- **Status:** Accepted, 2026-11-08. Built.

## Context

The first version kept a 24-hour HS256 JWT in `localStorage`: readable by any script on the page, with no logout, no revocation, and no way to cut off a disabled account before it expired. There is one API, and it already has a database.

## Decision

- Login creates a row in `sessions`. The browser's cookie holds 32 random bytes; the database stores only their SHA-256, so a database dump yields no live sessions.
- Cookie `medsum_session`: `HttpOnly`, `SameSite=Strict`, `Path=/`, `Secure` unless `COOKIE_SECURE=false` (local HTTP only).
- **Timeouts:** 30 minutes idle (sliding; the last-seen time written at most once a minute) and 12 hours absolute, one shift.
- **A new session on every login**, so a session can't be fixed in advance. Changing a password, disabling an account or changing its roles deletes the account's other sessions; logout deletes the row. Expired rows are purged.
- Each user can list their own sessions (device, IP, last seen) and end any of them, or all but the current one.

## Alternatives rejected

- **JWT in `localStorage`.** Readable by script; not revocable.
- **JWT in a cookie.** Still not revocable without a server-side list, which is a session table by another name. With one API and a database, the stateless benefit buys nothing.

## Consequences

- Covered: token theft by script, session fixation, stale access after someone leaves (revocation is immediate).
- Every request reads the session row; at this scale that costs nothing.
- `Secure` cookies assume TLS outside localhost, which is the deployer's job.

## What would change it

Several independent services that must verify identity without a shared database.
