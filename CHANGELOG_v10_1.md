# UniSubmit AI v10.1

Phase 2 of the restructuring plan: several universities on one installation.
Branch `phase-1-tooling-layout-migrations` (Phases 1 and 2 ship together in
pull request #3).

## Upgrade steps

- Nothing to run by hand: `docker compose up -d --build` applies migration
  `0002` and moves stored files (`unisubmit migrate && unisubmit relocate-files`
  in the `migrate` service). Back up the `pgdata` and `app_storage` volumes
  first.
- Existing data now belongs to "Default University" (`/u/default/`); every
  Super Admin is its University Admin.
- Create a Platform Admin with `unisubmit create-platform-admin --email …`
  (replaces `create-super-admin`) to rename that university or add others.
- Public URLs moved to `/u/<short name>/register` and
  `/u/<short name>/guidelines`. The old URLs redirect to the only university,
  or to the list on the home page when there are several.

## Tenancy (P2.1–P2.4, P2.6)

- New `universities` table. `university_id` on faculties, institutes,
  supervisors, students, submissions and admins; departments, programs and
  guidelines inherit it from their parent.
- Unit names and codes are unique per university (departments per faculty),
  supervisor emails per university; login emails stay unique everywhere, so
  login needs no university picker (`DEF-043`).
- Roles: `PLATFORM_ADMIN`, `UNIVERSITY_ADMIN` (was `SUPER_ADMIN`),
  `UNIT_ADMIN`, enforced by CHECK constraints: only a Platform Admin may
  belong to no university.
- The university is taken from the logged-in user, never from the request.
  Deactivating a university signs out its users on their next request and
  refuses their logins.

## Platform Admin (P2.5)

- `/platform/universities`: create a university with its first University
  Admin in one step, rename it, deactivate or reactivate it.
- AI Management moved from the University Admin to the Platform Admin.

## Public pages and scoping (P2.7, P2.8)

- `/u/<slug>/register` offers only that university's departments and
  institutes and rejects any other unit id; `/u/<slug>/guidelines` lists only
  its guidelines. The home page lists active universities.
- Reads of tenant data go through `queries.py`. Writes check ownership: a
  department can only be added to the admin's own faculty, a unit admin only
  to the admin's own units, a submission only with a supervisor of the
  student's university.

## Files and Qdrant (P2.9, P2.10, `DEF-042`)

- Files are stored under `storage/<university id>/<guidelines|submissions|reports>/`
  and rows hold relative paths, resolved with a containment check. The same
  rows now work in Docker and natively.
- `unisubmit relocate-files` moves files stored under pre-v10.1 absolute
  paths; it is safe to rerun.
- Every Qdrant point records its `university_id`.

## Tests (P2.11)

- `tests/tenancy/test_isolation.py`: two universities built by the same
  factory (so their units share names and codes), and one test per boundary:
  structure, unit admins, registration, guidelines, submissions, queue,
  supervisors, storage folders, platform access and deactivation. Removing a
  scope filter makes a test fail.
- Adoption is tested against `tests/fixtures/schema_v9_14.sql`, the schema
  v9.14 actually built.
- Result: 169 passed, 1 expected failure (`DEF-021`).

## Defects

- Closed: `DEF-042` (environment-specific paths), `DEF-043` (installation-wide
  uniqueness).
- `DEF-049`: `/admin/configuration` returned its 403 instead of raising it.
- `DEF-050`: adopting a pre-migration database compared it with the latest
  models, so every v9.14 database would have been refused once revision
  `0002` existed. Adoption now renames Postgres's own constraint names,
  stamps the baseline, upgrades to head and compares there, all in one
  transaction. Introduced and fixed before merge.

## Verified

A compose rehearsal on a database and storage volume written by the v9.14
image: adopted and upgraded to `0002`, two files relocated into
`1/guidelines/` and `1/submissions/` and downloadable, the old Super Admin
signed in as University Admin, a Platform Admin created a second university
whose registration page opened. A restart relocated nothing.

## Not in this phase

- `docker compose up -d` still starts the web app and worker in Docker on the
  Mac. With `DEF-042` closed they could run natively, but the uploads live in
  the `app_storage` volume; moving them out is a separate decision.
- Per-university upload caps and fair GPU scheduling arrive with the job queue
  (Phase 3).
