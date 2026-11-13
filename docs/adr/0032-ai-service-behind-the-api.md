# 0032. The browser never calls the AI service

- **Status:** Accepted, 2026-11-01; restated with authentication, 2026-11-08. Built, apart from binding to localhost and moving the development secrets out of the run scripts.

## Context

The AI service reads documents, calls the models and writes records. Its own checks are about documents, not people; authentication, permissions and organisation scope live in the Go API.

## Decision

- The browser talks only to the Go API. The Go API checks the session, the permission and the document's ownership, then calls the AI service's internal routes with a shared secret in `X-Internal-Token`, compared in constant time.
- The AI service trusts the Go API and takes only document or user ids, never paths or content from the browser.
- The AI service is to bind to `127.0.0.1` by default, and development secrets are to live in an untracked environment file per service with a committed example, not in the run scripts.
- Model output reaching the browser is contained as well: no page is model-written HTML (see [0020](0020-pages-built-by-code.md)).

## Alternatives rejected

- **The frontend calls the AI service directly.** Every authorization rule would have to be repeated in Python, and the model-facing service would face the network.
- **Mutual TLS between the services.** More than one machine needs; the shared secret plus a localhost bind fits a single host.

## Consequences

- One place to audit access (see [0031](0031-append-only-audit-log.md)).
- A leaked internal token gives full access to the AI service's routes, which is why it should only listen on localhost.

## What would change it

The services running on separate hosts, which would call for TLS and a stronger service identity.
