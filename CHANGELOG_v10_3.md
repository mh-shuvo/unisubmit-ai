# UniSubmit AI v10.3

Phase 4 of the restructuring plan: security essentials. Branch
`phase-1-tooling-layout-migrations`, on top of Phases 1 to 3 (pull request #3).

## Upgrade steps

- `docker compose up -d --build` applies migration `0005` (auth epochs, login
  counters, `audit_events`). Back up the `pgdata` volume first.
- Everyone is signed out once: sessions from before the upgrade carry no auth
  epoch. A page left open from before answers "Your form has expired" when
  submitted; reloading it fixes that.
- Passwords are unchanged. Each one is rehashed with Argon2id at its owner's
  next login.
- New optional settings: `UNISUBMIT_SESSION_HTTPS_ONLY` (set it to `true`
  behind HTTPS) and `UNISUBMIT_LOG_JSON` (one JSON object per log line).
- New dependencies: `pwdlib[argon2]` and `structlog` (`uv sync` installs them).

## Passwords and logins (P4.1, P4.4)

- New hashes are Argon2id. The old PBKDF2 format still verifies and is replaced
  at the next successful login (`DEF-040`).
- Ten wrong passwords in a row lock an account for 15 minutes; while it is
  locked even the right password is refused. A login with an unknown email
  takes as long as one with a wrong password.
- Unit admins created by a University Admin need a password of at least 12
  characters, like the other admin accounts.

## CSRF and sessions (P4.2, P4.3)

- Every form carries a per-session token, and every POST is refused with 403
  without it (`DEF-041`). Scripts may send it as `X-CSRF-Token`. Signing out is
  a POST; the old GET links answer 405.
- Every login starts a new session. Sessions last 8 hours, and the cookie is
  `Secure` when `UNISUBMIT_SESSION_HTTPS_ONLY` is set.
- Students (on their profile) and admins (on the dashboard) can change their
  password; that signs out their other sessions and keeps this one.

## Errors and logs (P4.6, P4.7)

- One error page for every refusal and missing page. An unexpected error
  shows only a reference, the request ID; the traceback goes to the log.
- Broad `except Exception` blocks are narrowed to the errors they handle, and
  Ruff's `BLE` rule keeps new ones out (the AI service's legacy module is
  exempt until Phase 6).
- Logs go through structlog, readable or JSON. Each request gets an ID (the
  caller's `X-Request-ID` if it is safe), returned in the response and bound
  to every log line. A job remembers the ID of the request that queued it, the
  worker logs under it, and the AI client sends it on, so one ID follows a
  thesis from upload to the model. The AI service prefixes its log lines with
  it.
- Security events are logged: logins (succeeded, failed, locked), permission
  denials, password changes and every audited admin action. Passwords,
  cookies, document text and prompts are never logged.
- Responses carry `X-Content-Type-Options: nosniff`.

## Audit log (P4.8)

- `audit_events` records who did what, when, under which request ID: creating,
  renaming and switching universities, creating faculties, departments,
  institutes, unit admins, programs, supervisors and submission
  configurations, every guideline change (upload, rule review, manual rules,
  activation, restrictions, parse requests) and admin password changes. Each
  row is written in the same transaction as the change.

## Uploads and downloads (P4.5)

- Password-protected PDFs were already refused; downloads already resolved
  stored paths inside the storage root and were sent as attachments. Tests now
  hold all three in place.

## Prompt injection (P4.9)

- Text a reader cannot see (white, fully transparent, under 4 pt or off the
  page) is found while the PDF is read. The report shows a warning with the
  pages, and fragments of 12 characters or more are left out of the text sent
  to the model.
- The model's results are forced into the semantic category with a known
  status (anything else counts as NOT VERIFIED). They are only ever added to
  the deterministic results, so they can make the outcome stricter but never
  turn a failure into a pass.

## Tests

- `tests/integration/test_security.py`: legacy and Argon2 logins, lockout and
  its expiry, the CSRF meta-test and refusals, POST-only logout, a token in
  every form on the guideline page, other sessions ended by a password change,
  a new session at login, the 8-hour cookie, downloads, error pages, request
  IDs in responses, jobs and AI calls, the audit meta-test and two audited
  actions, hidden text, and model rows that try to overturn a failed rule.
- AI service: request IDs in its log lines, and unsafe IDs dropped.
- Every test client now sends the CSRF token, as a browser would.
- Result: 236 passed, 1 expected failure (`DEF-021`).

## Defects

- Closed: `DEF-040` (hand-written PBKDF2), `DEF-041` (no CSRF tokens, GET
  logout).

## Not in this phase

- Users and admins cannot yet be deactivated individually from a page; when
  that arrives it should raise `auth_epoch` too.
- Unused templates (`admin.html`, `admin_academic.html`, `admin_ai.html`,
  `admin_people.html`, `admin_configuration.html`, `ai_status.html`) got CSRF fields but are rendered by no route; Phase 5 can
  delete them.
- Rate limiting by IP address belongs to nginx (Phase 7).
