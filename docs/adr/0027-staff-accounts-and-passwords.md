# 0027. Staff accounts: email and password, created by an admin

- **Status:** Accepted, 2026-11-08; confirmed 2026-11-13, when a second factor was dropped from the scope. Built, except the per-IP rate limit.

## Context

The first version logged in with a phone number and an OTP field that nothing checked: anyone who could reach the API could be anyone. Constraints: local only, no paid services (no SMS gateway, no hosted identity provider, no email sending), one Go API in front of Postgres, small scale, and a repository where the security reasoning should be readable.

## Decision

- **Email and password.** Phone and OTP are dropped: SMS costs money, and an unverified OTP is no factor at all.
- **No self sign-up.** Only an admin creates an account (`medsumctl` for the first admin of an organisation, the Staff page after that). Creating one returns a **one-time setup link** (72 hours) that the admin hands to the person, who sets their own password; the admin never knows it. A password reset is the same kind of link (24 hours), issued by an admin, since there is no email. The token sits in the URL fragment, which browsers don't send to servers, and only its SHA-256 is stored. No default credentials exist anywhere.
- **Hashing:** argon2id with OWASP parameters (19 MiB, t=2, p=1), stored in PHC format so parameters can be raised and old hashes re-hashed on the next login.
- **Password rules** (NIST 800-63B style): 12 to 128 characters, no composition rules, refused if it is in an embedded list of common passwords, is a short pattern repeated ("abcabcabcabc"), or contains the email's local part.
- **Brute force and enumeration:** one generic "invalid email or password"; an unknown email gets a dummy argon2 verification, so timing doesn't reveal which accounts exist; an account locks for 15 minutes after 5 consecutive failures; an in-memory per-IP limit on `/auth/*` (10 attempts a minute) is part of the decision.
- **Development:** `medsumctl seed-dev` refuses unless `MEDSUM_ENV=dev`, and takes the password from `MEDSUM_DEV_PASSWORD`.

## Alternatives rejected

- **Phone and OTP.** Costs money per message; the unchecked version was worse than nothing.
- **A hosted identity provider or SSO.** A paid or cloud dependency; a hospital deployment would integrate its own.
- **A TOTP second factor.** Designed (authenticator app, recovery codes, required for admins), then dropped from the scope; the README states there is no second factor.

## Consequences

- Covered: credential stuffing and guessing (lockout, rate limit, password rules), account enumeration (generic errors, dummy hash), stolen database rows (hashed passwords and setup tokens).
- Open, and stated in the README: licences are not verified; links are handed over in person, since there is no email; no second factor.

## What would change it

A real deployment: the hospital's own identity provider, email delivery for links, or a policy requiring a second factor.
