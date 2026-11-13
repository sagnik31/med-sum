# 0029. CSRF: SameSite cookies, a custom header and an origin check

- **Status:** Accepted, 2026-11-08. Built.

## Context

A session cookie is sent with every request to the API, including requests a hostile page makes the browser send.

## Decision

- In development, Vite proxies `/api` to the Go server, so the app and the API share an origin and the cookie is first-party.
- Every state-changing request (anything but GET, HEAD and OPTIONS) must carry `X-Requested-With: med-sum`, and its `Origin`, when it has one, must be in `ALLOWED_ORIGINS`. A cross-site form can't set the header, and a cross-site `fetch` with the header needs a preflight that CORS refuses.
- Browsers always send `Origin` on cross-origin requests, so a request without one is same-origin or not from a browser (the dataset loader, curl) and can't be CSRF.
- With `SameSite=Strict` as well, no CSRF token is needed.
- CORS origins come from `ALLOWED_ORIGINS` (comma-separated), never `*` with credentials.

## Alternatives rejected

- **A synchronizer CSRF token.** More state and more client code, for protection the three layers above already give.
- **SameSite alone.** Depends on every browser implementing it fully; the header and origin check don't.

## Consequences

- API clients (Postman, the loader) must send the header on non-GET requests; the README says so.
- A GET must never change state, which the routes follow.

## What would change it

Serving the API to third-party browser apps, which would need tokens or a different scheme.
