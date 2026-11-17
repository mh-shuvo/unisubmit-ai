# UniSubmit AI v10.4

Phase 5 of the restructuring plan: the core split into modules. Branch
`phase-1-tooling-layout-migrations`, on top of Phases 1 to 4 (pull request #3).
No URL, form or page changes, apart from two new health probes and page links
on two long lists.

## Upgrade steps

- Nothing to run: no migration, no new setting, no new dependency.
- New `GET /health/live` (the process answers) and `GET /health/ready` (the
  database answers) for a load balancer or `docker compose` health check.

## Layout (P5.1-P5.5)

- `main.py` only builds the app. The routes moved, unchanged, into one router
  per area: `auth/` (logins, logout, passwords), `tenancy/` (Platform Admin),
  `organisation/` (faculties, departments, institutes, unit admins, programs,
  supervisors), `guidelines/` (configurations, versions, parsed rules),
  `submissions/` (uploads, reports, downloads, the unit's list), `portal/`
  (home, registration, dashboards) and `system/` (AI status, queue, health).
- Shared code: `core/templating.py`, `core/security.py` (was `security.py`),
  `auth/dependencies.py` (who is signed in), `tenancy/service.py` (was
  `tenancy.py`), `guidelines/profile.py` (parsed-profile helpers),
  `submissions/service.py` (versions, caps, the validation job),
  `validation/semantic.py` (the AI payload and statistics); `store_pdf` is in
  `storage.py`.
- `tests/unit/test_routes.py` lists every route; the split left the table
  unchanged.
- Kept flat for now: `models.py`, `queries.py`, `queue.py`, `handlers.py`,
  `storage.py`, `audit.py` and the templates folder.

## Rules (P5.6)

- `format_validation.py` is now `validation/rules/`: `values` (numbers,
  operators, targets), `results` (result rows), `sections` (finding sections
  and headings), `typography`, `layout`, `structure`, `cover`, and `engine`,
  which looks up each rule's check by its kind. The 700-line branch chain was
  split by a script, so each branch's code moved verbatim; the 767-line rule
  suite passes with only its imports changed.

## Boundaries and lists (P5.7, P5.8)

- An import-linter `layers` contract fixes the order: pages and the worker's
  handlers, submissions, validation, guidelines, organisation, tenancy, auth,
  integrations, models, core. A lower module importing a higher one fails CI.
- The unit's submission list and queue page load related rows up front and
  show 100 rows per page; a test proves the number of SQL statements is the
  same for 10 and 50 rows. The admin dashboard counts with SQL.

## Types (P5.9)

- Every core module is checked by strict mypy, including the models, PDF
  reading, rule checks, validators, reporting and the AI and mail clients.
  Only the AI service's `main.py` stays exempt, until Phase 6.

## Removed

- Seven templates no route rendered, and `ai.py`, the direct Ollama client
  they used.

## Defects

- Closed: `DEF-044`. The page parsing of semantic rules is one helper that
  reads a number or a string as one page.
- Found and fixed: Phase 4's CSRF and audit meta-tests passed without
  checking anything, because FastAPI 0.16x nests an included router's routes
  instead of copying them into `app.routes`. They walk the nested routers now
  (`support.api_routes`), and the audit test was seen red with one call
  removed. Not registered as a defect: the protection itself was in place.

## Tests

- New: `unit/test_routes.py`, `unit/test_profile.py`,
  `integration/test_admin_lists.py`.
- Result: 243 passed, 1 expected failure (`DEF-021`).

## Not in this phase

- Older design documents (`docs/validation_defects_and_fixes.md` and others)
  still name `app/format_validation.py` and its private helpers; they record
  the code as it was when each defect was fixed.
- Routers still hold some business logic (the first-submission checks, the
  rule-review merge); moving it into services is left for when it changes.
