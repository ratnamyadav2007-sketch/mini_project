# Backend API

A Python API foundation using FastAPI, Pydantic Settings, SQLAlchemy, and Alembic.

## Setup

Install [uv](https://docs.astral.sh/uv/) and Node.js/npm, then create the
virtual environment and install the locked dependencies:

```powershell
uv sync --all-groups
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

The default `.env.example` uses a local SQLite database at
`var/backend.sqlite3`; no database server is needed. Set `DEMO_PASSWORD` in
`.env` to a demo-only password of at least 12 characters before seeding the
sample family. PostgreSQL remains available by setting `DATABASE_URL`.

## Run

The recommended local setup uses a single origin: FastAPI serves the built
frontend from `frontend/dist/` (or the source frontend if no build exists), and
its API remains under `/api/v1`. Run the migrations and frontend build, then
start Uvicorn bound to loopback:

```powershell
make setup
make run
```

Open `http://127.0.0.1:8000` for the frontend and
`http://127.0.0.1:8000/api/v1/health` for the API health check. Leave the
`make run` process running while using the app.

To run Uvicorn directly instead:

```powershell
uv run alembic upgrade head
npm --prefix frontend ci
npm --prefix frontend run build
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Common project commands:

```text
make setup
make migrate
make run
make seed
make test
make test-e2e
make coverage
make openapi
make postman-contract
```

For Windows Command Prompt without GNU Make, the equivalent setup and run
commands are:

```cmd
uv sync --all-groups
if not exist .env copy .env.example .env
npm --prefix frontend ci
npm --prefix frontend run build
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`make setup` installs the locked Python and npm dependencies, creates `.env`
from `.env.example` if needed, builds the fingerprinted frontend, and installs
pre-commit hooks when run inside a Git checkout. `make run` runs migrations
plus the frontend build before starting the app. GNU Make is optional; the
individual `uv` and `npm` commands above can be run directly.
The build minifies CSS/JavaScript and fingerprints assets; FastAPI serves
fingerprinted assets with immutable one-year caching, revalidates HTML and
unfingerprinted assets, and compresses larger responses with gzip. Insights,
chat, and Chart.js load only when those routes are opened. CORS accepts the
`http://localhost` origin only; the same-origin frontend does not use CORS.

## Frontend/API contract

Shared API, serialization, error, session-cookie, and CSRF conventions are
documented in [API_CONVENTIONS.md](./API_CONVENTIONS.md). The Auth and Profiles
OpenAPI contract is generated from the FastAPI application:

```powershell
make openapi
make postman-contract
```

Review `openapi/auth-profiles.openapi.json` with the frontend before changing
request or response shapes. The generated
`postman/auth-profiles.postman_collection.json` includes the contract's Auth
and Profiles operations; Postman stores the session cookie automatically.

## Frontend API client

The browser client and shared state/UI helpers live in `frontend/js/`. API
requests use the same-origin `/api/v1` base, same-origin cookies, automatic
CSRF challenges for mutations, JSON error objects, and a 10-second default
timeout. Non-auth requests include the active profile ID from the in-memory
store; profile-scoped endpoints continue to authorize using the UUID in the
path. Shared global handlers show access/rate-limit notices, redirect
unauthenticated users to sign-in with a safe `returnTo` path, and report
connectivity failures. The health, loading, and error-state test page is at
`/api-client-test.html`; append `?mock=1` to use fixture responses without a
backend API. Buttons on that page exercise each global handler, including the
401 return-to-login flow.

The signed-in shell exposes diet/goals at `#/diet`, charts at `#/insights`,
and the wellness assistant at `#/chat`. Chart.js is bundled and served from
the same origin to satisfy the local content-security policy; the mock API
also supplies contract-shaped responses for these screens.

Run the browser-client and state-store unit tests with:

```powershell
cd frontend
npm test
```

Install the Playwright browsers once, then run the real-API browser journeys
with:

```powershell
npx playwright install chromium firefox webkit
npm run test:e2e
```

The E2E runner starts FastAPI against a temporary SQLite database with test-only
keys. Projects cover Playwright Chromium, installed Chrome and Edge, Firefox,
WebKit, and an iPhone-sized Chromium viewport. WebKit is an engine-level
Safari approximation; native Safari is not available on Windows. The browser
journey covers registration, onboarding, CSRF/cookie flags, family invitations
and consent, private-versus-shared visibility, XSS-safe rendering, rejected
upload types, SOS demo delivery, logout, and mobile overflow. Backend tests
continue to cover rate limiting and the full role/visibility/action matrix.

## Database migrations

```powershell
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "describe schema change"
```

Migrations create and evolve the application schema. Apply the current head
before starting database-backed routes; SQLite is supported for tests and
local scripts, while PostgreSQL is the production target.

Seed the record types, explicitly demo-only reference ranges, and illustrative
food/nutrient rows with:

```powershell
uv run backend-seed
```

The reference ranges and nutrient values are labeled as illustrative examples;
they are not clinical guidance.

Seed the sample family after configuring `DEMO_PASSWORD` in `.env`:

```powershell
make seed-demo
```

This creates the local-only `Sample Demo Family` and two demo users; it is
idempotent and uses the same configured demo password for both accounts.

The typed `Repository` in `app.db.repository` provides create, get, list, update,
and delete operations. Deletion is soft for mutable domain/reference records;
audit log deletion is physical. CRUD tests use an isolated in-memory SQLite
database with foreign-key enforcement.

## Core schema

The migration creates UUID-keyed `users`, `member_profiles`, `families`,
`family_memberships`, `health_records`, `attachments`, `share_grants`,
`emergency_contacts`, `appointments`, `reminders`, `diet_plans`, `meal_items`,
`goals`, `goal_logs`, `badge_awards`, `sos_events`, and `audit_logs` tables. The reference
tables `record_types`, `reference_ranges`, and `foods` support health-record and
meal data. Mutable records have creation/update timestamps and a nullable
`deleted_at`; audit logs have a creation timestamp.
Health records are indexed by `(profile_id, recorded_at)`, `(profile_id, type,
recorded_at)`, and `type`; appointment and reminder scheduling queries also
have profile/date/status indexes.

## Authentication

Apply the latest migration, then run the API as described above. Auth endpoints
are under `/api/v1/auth`: `POST /register`, `/login`, `/logout`, and
`/reset-password`; `GET /me` returns the signed-in user. First obtain a
single-use CSRF challenge from `GET /api/v1/auth/csrf`, then include its
`csrf_token` in the `X-CSRF-Token` header on register, login, and password
reset. Register and login return a new session CSRF token; use that token for
logout. The session is an HttpOnly, SameSite=Lax cookie.

Passwords must be 12–128 characters and are hashed with Argon2id. Login failures
are limited to five attempts per email in a 15-minute window, followed by a
15-minute lockout. Registration returns a high-entropy recovery code once; store
it securely because password reset consumes it and invalidates all sessions.
The frontend keeps the session CSRF token in memory only; authenticated pages
restore it through `GET /api/v1/auth/csrf/session` after `GET /api/v1/auth/me`.
Other tabs are notified when a session is signed out, while the server remains
the authority for revocation.

## Profiles and onboarding

The app loads member profiles from `GET /api/v1/profiles`. Onboarding saves
each completed step with `PATCH /api/v1/profiles/{profile_id}`, so a refresh
resumes from the server-stored step. Profile preferences retain the
relationship, avatar colors, and completion state. Adding a dependent creates
a family if necessary, then creates the dependent profile under that family;
family owners and guardians can manage those dependent profiles. The member
switcher and profile-scoped screens use the active profile ID from the shared
in-memory store.
The default cookie is suitable for localhost HTTP. Set `AUTH_COOKIE_SECURE=true`
when serving over HTTPS. Session lifetime, CSRF challenge lifetime, and lockout
thresholds can be configured with the corresponding `AUTH_*` settings.

## Permissions, audit, and encryption

Profile-scoped record routes should use `require_record_access(Model, resource,
Action.READ)` (or `WRITE`/`DELETE`) as a FastAPI dependency; the health-record
shortcut is `require_health_record_access(Action.READ)`. It loads visibility from the database,
evaluates the relationship and grant rules in `app.security.permissions`, and
appends an access decision to the audit trail before returning or denying.
Profile owners have full control; family membership does not automatically
grant access to another member's records. Adult, Viewer, and Guardian accounts
need explicit record shares or profile-level consent for the relevant resource.
Linked family Owners and Guardians can manage unclaimed dependent profiles
(profiles without a user account). Viewer and Emergency Contact roles are read-only; Emergency
Contacts are additionally limited to emergency resources. Unknown roles and
mismatched relationships are denied by default.
`GET /api/v1/audit/access-history` returns only the authenticated user's own
profile history.
Health records, attachments, emergency contacts, appointments, reminders,
diet plans, goals, badges, and SOS events have a visibility value that defaults
to Private. Meal items inherit their diet plan's visibility.

Audit events are appended through `app.security.audit`; both the repository
and database triggers reject updates and deletes.

Before encrypting fields, configure `MASTER_ENCRYPTION_KEY` with the base64
encoding of a randomly generated 32-byte key, and set its
`MASTER_ENCRYPTION_KEY_VERSION`. Generate a key with:

```powershell
python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

`app.security.field_encryption` provides AES-256-GCM field encryption with a
random per-user data key, user/field-bound associated data, and the wrapped key
stored on the user record. Encrypted fields should be stored as the returned
nonce and ciphertext bytes. For rotation, securely provide
`OLD_MASTER_ENCRYPTION_KEY`, `NEW_MASTER_ENCRYPTION_KEY`, and
`NEW_MASTER_ENCRYPTION_KEY_VERSION` as environment secrets, then run
`uv run backend-rotate-keys`. This rewraps data keys only; it does not decrypt or
rewrite protected field ciphertext.

## Profiles, health records, and attachments

`/api/v1/profiles` lists the signed-in user's profiles and linked dependents.
Guardians can create a dependent profile with `POST /api/v1/profiles` when a
Guardian membership in the specified family has already been provisioned.
Profile-scoped health-record creation is available at
`POST /api/v1/profiles/{profile_id}/records`; records can be read, updated, or
soft-deleted at `/api/v1/records/{record_id}`. A profile timeline is available
at `GET /api/v1/profiles/{profile_id}/timeline`, with `type`, `start_year`,
`end_year`, `search`, `offset`, and `limit` query parameters. Cursor pagination
uses `cursor` and returns `next_cursor`/`has_more`; do not combine `cursor` with
a non-zero `offset`. `GET /api/v1/meta/record-types` provides the record types
and field schemas used by clients to build forms. Record payloads are encrypted
at rest with AES-256-GCM; configure the master key before creating records.
Record timestamps and visibility remain queryable metadata.

Share a record with `POST /api/v1/records/{record_id}/share` using a recipient
email, a delegable role, and optional permissions and expiry. Revoke a grant at
`DELETE /api/v1/records/{record_id}/share/{grant_id}`. A share makes the record
Selected-visible to the specified recipient only while the grant is active.

Attachments are uploaded as multipart files to
`POST /api/v1/profiles/{profile_id}/attachments` and downloaded through
`GET /api/v1/attachments/{attachment_id}/download`. Include the optional
multipart `record_id` field to link an upload to a record in the same profile.
Uploads are limited to 10 MiB (or a lower configured
`ATTACHMENT_MAX_SIZE_BYTES`) and accept valid PDF, JPEG, and PNG signatures
only. Files are encrypted before being written to `ATTACHMENT_STORAGE_PATH`
(default `var/attachments`); the path should be on private local storage and
excluded from public/static serving.

## Emergency response, appointments, and reminders

Configure a random `SOS_SIGNING_KEY` of at least 32 characters and the
`PUBLIC_BASE_URL` before enabling SOS. `POST /api/v1/sos` accepts a profile ID,
creates a time-limited, signed bearer URL, and queues a notification for each
active emergency contact in priority order. The localhost/demo notifier returns
delivery status in the response and appends each notification to
`NOTIFICATION_LOG_PATH` (default `var/notifications.log`). Protect that log:
it contains contact destinations and the bearer link. A real email/SMS gateway
can replace the `Notifier` implementation without changing the API or outbox.
The public `GET /api/v1/sos/{token}` is rate limited per client address and
returns only the emergency card. SOS creation also returns `public_page_url`,
which opens the no-login `public-sos.html` card view. Neither response exposes
SOS notes or location.
Links expire after one hour by default, can be configured up to 24 hours, and
are invalidated when the event is resolved through
`POST /api/v1/sos/{event_id}/resolve`. Authenticated clients can poll
`GET /api/v1/sos/{event_id}` for delivery status; the OpenAPI-safe equivalent
is `GET /api/v1/sos/events/{event_id}`. The signed-token and UUID routes are
distinguished by token format.

Emergency contacts are managed with `GET`/`POST
/api/v1/profiles/{profile_id}/emergency-contacts` and `PATCH`/`DELETE
/api/v1/emergency-contacts/{contact_id}`. The card uses the profile name and
date of birth, latest blood-group record, and current allergy, condition, and
medication records. Seed the `blood_group` record type and add a health record
of that type to populate the card.

Appointments are managed under
`/api/v1/profiles/{profile_id}/appointments` and
`/api/v1/appointments/{appointment_id}`. The profile calendar can be exported
as an access-filtered ICS feed at
`GET /api/v1/profiles/{profile_id}/appointments.ics`; `GET /api/v1/appointments`
also accepts the `profile_id`, `from`, and `to` query parameters for calendar
views. A profile emergency card is available to authorized signed-in users at
`GET /api/v1/profiles/{profile_id}/emergency-card`; the frontend refreshes its
offline copy after profile, contact, or health-record changes. Reminders are managed
under `/api/v1/profiles/{profile_id}/reminders` and
`/api/v1/reminders/{reminder_id}`; use the snooze and complete actions at
`POST /api/v1/reminders/{reminder_id}/snooze` and
`POST /api/v1/reminders/{reminder_id}/complete`. Authenticated clients can poll
`GET /api/v1/reminders/due` every 30 seconds; pass the last successful poll
timestamp as `since` to collect recently delivered notifications. Recurrence
supports daily, weekly, and monthly intervals. When the scheduler catches up
after missed intervals, it sends one notification and advances to the next
future occurrence rather than sending a burst of missed reminders.

Reminder scheduling requires a running Redis broker configured by
`CELERY_BROKER_URL` (default `redis://localhost:6379/0`) and a Celery worker
with Beat. After applying migrations and starting Redis, run locally on
Windows:

```powershell
uv run celery -A app.celery_app:celery_app worker --beat --pool=solo --loglevel=INFO
```

The Beat schedule checks for due reminders every minute. The scheduler writes
delivery records to the database outbox before dispatching; failed demo-log
writes stay pending for a later retry. Reminder notifications are logged by
the same demo notifier and include the reminder message, so protect the
notification log accordingly.

## Family groups and consent

Create a family with `POST /api/v1/families`. Family members can be listed at
`GET /api/v1/families/{family_id}/members`. The family page loads the member
list, active consents, recent authorized updates, appointment summaries, and
goal counts from one
`GET /api/v1/families/{family_id}/dashboard` request. Owners and Guardians can create a
role-scoped invitation at `POST /api/v1/families/{family_id}/invites`; the
response contains a high-entropy, one-time-display invite code, while only its
hash is stored. Share the code out of band on localhost. A signed-in user
accepts or declines it at `POST /api/v1/family-invites/accept` or
`POST /api/v1/family-invites/decline`. Codes expire after 72 hours by default
(configurable per invite up to 30 days). Managers can update a member's role
with `PATCH /api/v1/families/{family_id}/members/{profile_id}`; a member can
leave, or a manager can remove them, with
`DELETE /api/v1/families/{family_id}/members/{profile_id}`.

Being in the same family does not automatically reveal another account's
health data. A profile owner can grant read-only resource consent through
`POST /api/v1/families/{family_id}/consents` with the sharing profile,
recipient profile, and one or more of `health_records`, `appointments`, and
`goals`; visibility still applies, so Private records remain private. Consent
can be reviewed at `GET /api/v1/families/{family_id}/consents`, partially
updated with `PATCH /api/v1/families/{family_id}/consents/{grant_id}`, or
revoked with `DELETE /api/v1/families/{family_id}/consents/{grant_id}`.
Profile-level and individual record shares are managed separately.

The dashboard returns member identity and role information but no health
profile details for another account. Recent record metadata, upcoming
appointments, and goal status counts are included only when the caller has
consented access. A Guardian can create dependent profiles for children
without their own account using `POST /api/v1/profiles` after joining as a
Guardian. Adults or elders with their own account join through an invite and
explicitly consent to the Guardian's access from their own account.

## Diet plans and wellness goals

Seed the illustrative food catalogue with `uv run backend-seed`. Generate a
seven-day plan with
`POST /api/v1/profiles/{profile_id}/diet/plans/generate`, supplying `weight_kg`,
`height_cm`, and `activity_level` (`sedentary`, `light`, `moderate`, or `high`).
The profile must have a date of birth and be an adult. The demo estimate uses a
simplified Mifflin-St Jeor calculation, activity multipliers, and general
protein/fat targets. It is not clinical nutrition advice. Every plan response
includes a disclaimer, daily and weekly nutrient totals, safety notes, and
foods excluded by the rule engine.

Allergy exclusions come from health records and match food allergen tags
(including common aliases such as tree nuts/almond). Active condition records
are compared with each food's `excluded_conditions`, and lab records can carry
an explicit `dietary_exclusions` list of food names. These exclusions are
applied before meals are selected and cannot be overridden by meal moves or
swaps. Raw laboratory values are not clinically interpreted. If a condition
or lab record contains an explicit `nutrition_targets` object, its supported
keys (`calories_kcal`, `protein_g`, `carbohydrate_g`, `fat_g`) override the
corresponding demo estimates; calorie targets must be 1,000–6,000 and macro
targets 0.1–500. Conflicting or out-of-range recorded targets fail generation
instead of being silently ignored. Verify any recorded targets with a
clinician.

List a profile's plans at
`GET /api/v1/profiles/{profile_id}/diet/plans` and retrieve one at
`GET /api/v1/diet/plans/{plan_id}`. Move a meal with
`POST /api/v1/diet/plans/{plan_id}/meals/{meal_id}/move` (date, meal type, and
board order), or swap two meals with the corresponding `/swap` endpoint. The
frontend-compatible aliases are `POST /api/v1/diet/plans/generate?profile_id=...`
and `PATCH /api/v1/diet/plans/{plan_id}/meals/{meal_id}`. Moves are limited to
the stored plan week; nutrient totals are recalculated from the food catalogue.
Meal responses include server-provided allergen labels and warning flags.

Create and list `steps`, `water`, `sleep`, and `weight` goals at
`POST`/`GET /api/v1/profiles/{profile_id}/goals`. Add or replace a daily value
with `POST /api/v1/goals/{goal_id}/logs`, optionally specifying `logged_on`.
There can be at most one value per goal per date; logging the same date again
updates that value. Step, water, and sleep streaks count consecutive days that
meet the goal target; weight streaks count consecutive daily weigh-ins. Badge
rules award `first_goal_log`, `goal_target_met`, and `seven_day_streak` once
per profile; the demo weight target badge uses a one-kilogram tolerance. Read
daily values at `GET /api/v1/goals/{goal_id}/logs`. View awards at
`GET /api/v1/profiles/{profile_id}/badges`.

## Insights, family comparison, and chat

Read a metric time series with
`GET /api/v1/profiles/{profile_id}/insights/{metric}`. Optional `start_date`,
`end_date`, `offset`, and `limit` filter the series. To select another member,
pass `member_id`; only records readable by the signed-in user are returned.
Each point carries its matching reference-range flag (below, within, above, or
unknown), source population, and source note. Trends compare the first and last
point on the selected page. Improvement is reported only where two-sided
reference ranges allow a normalized distance-to-range calculation; it is not a
clinical assessment.

Reference ranges can optionally declare `sex`, `age_min`, and `age_max`. The
most specific matching range is selected for each measurement date. Family
comparison is available at
`GET /api/v1/families/{family_id}/insights/compare?metric=...`, with optional
date filters and repeated `member_ids`. The signed-in user must be a family
member. The response includes the user's own accessible data plus other family
members' data only where explicit health-record consent/share makes each
record readable. Comparisons require matching sex- and age-specific,
two-sided reference ranges; raw readings are not returned. Outputs are
normalized interval values and per-member improvement deltas, not a ranking.
Reference ranges are illustrative unless replaced with clinically reviewed,
population-specific data.

The frontend keeps an in-memory, 10-second GET response cache (maximum 100
entries), deduplicates concurrent reads, keys entries by active profile, and
clears the cache on writes and uploads. Authentication and due-reminder polling
are never cached. Search and chart filters debounce rapid changes.

The chart screen uses
`GET /api/v1/insights?metric=...&range=30d&members=profile-uuid,...`.
Supported ranges are `7d`, `30d`, `90d`, `1y`, and `all`. Each requested
family profile returns either an authorized data series or an empty
consent-locked series; points include numeric reference bounds when a matching
range exists. The response also includes trend/improvement summaries and the
applicable reference-range populations.

The read-only assistant is at `POST /api/v1/chat/messages` with `profile_id`
and `message` (CSRF protected); `POST /api/v1/chat` remains a compatible alias.
Replies include safe navigation/reminder action cards. It routes
emergency/distress language first and returns guidance to contact local
emergency services; the chat never triggers SOS or contacts anyone. It can
summarize an authorized blood-pressure, glucose, or temperature reading, list
authorized pending reminders, or provide static general sleep, hydration, and
activity education. Data and reminder tools enforce the same per-record
visibility and consent checks as their APIs.

Rule-based responses work offline. An optional model adapter can be configured
with `CHAT_MODEL_URL` and `CHAT_MODEL_API_KEY`; model use is off unless the
request explicitly sets `allow_model: true`, and only for education responses.
The adapter sends a fixed topic label, never the user's message, profile ID,
readings, or reminders. Prefer a local HTTPS/localhost model endpoint. Remote
HTTPS endpoints are an explicit data-processing choice; review the provider's
privacy terms before configuring one. Unsafe or unavailable model output falls
back to the built-in education text. The endpoint accepts JSON
`{"prompt":"...","max_tokens":180}` and may return `{"response":"..."}`,
`{"output_text":"..."}`, or an OpenAI-style `choices` response.

## Quality checks

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pre-commit install
```

## Release hardening, exports, and account deletion

Responses include `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`, `Permissions-Policy`, and a restrictive Content Security
Policy. HTTPS responses additionally receive HSTS. CORS is limited to
`http://localhost` and only the API methods/headers used by this application.
The wellness assistant is limited to 30 requests per user per minute and
profile exports to five per user per hour; rate-limit identifiers are stored
as SHA-256 digests. Login lockout and the public SOS per-address limit remain
separate.

Authenticated users can export a profile they are authorized to read in three
formats:

- `GET /api/v1/profiles/{profile_id}/export.json` returns profile metadata,
  accessible health records, appointments, reminders, emergency contacts,
  goals/logs, and attachment metadata.
- `GET /api/v1/profiles/{profile_id}/export.fhir` returns a FHIR-style
  `Bundle` with Patient, Observation/Condition and related resources. This is
  an interoperability-oriented representation, not a claim of full FHIR
  validation.
- `GET /api/v1/profiles/{profile_id}/export.pdf` downloads a printable summary
  of accessible health records.

All exports apply profile and per-resource visibility/consent checks, are
audited, and use `Cache-Control: no-store`. Attachments are represented by
metadata; download their encrypted bytes separately through the authorized
attachment endpoint.

`DELETE /api/v1/auth/account` requires the session CSRF token, immediately
disables authentication, revokes sessions and incoming shares, removes active
family memberships, and schedules purge 30 days later. Beat runs the purge
daily; run it manually with `uv run backend-purge-deleted`. Purge removes the
profile and its attachments (including encrypted files), family invitations,
and account login data. An anonymized user tombstone remains because immutable
audit entries retain their actor reference. Its wrapped data key is retained
only when another member's encrypted data still depends on it, preserving
those records without retaining the deleted user's credentials or identifying
profile data.

## Demo, backup, dependency audit, and release checks

Set `DEMO_PASSWORD` to a 12-character-or-longer demo-only password, then run:

```powershell
make seed
make demo
```

`make seed` applies reference data and creates two local demo accounts
(`demo.owner@example.com` and `demo.family@example.com`), one family, a private
demo wellness appointment, and a private hydration goal for each profile.
Demo profiles use a clearly fictitious date of birth so diet planning can be
shown. Existing demo accounts are not reset; `DEMO_PASSWORD` is used only when
an account is first created. `make demo` walks through records, emergency-card
setup, appointments, reminders, goals, diet planning, insights, chat, family
dashboard, export, and logout using fictitious local data. The reminder is
scheduled for the following day; the demo intentionally does not trigger SOS
notifications. Do not use real personal data in the demo database.

`make backup` writes `var/backend-backup.dump`; `make restore` replaces the
configured database from that file and is destructive. You can also use
`uv run python scripts/db_backup.py backup <path>` and
`uv run python scripts/db_backup.py restore <path> --yes`. PostgreSQL
backup/restore additionally requires `pg_dump`/`pg_restore` on `PATH`; SQLite
backup uses the online backup API. The database backup does not contain
attachments: back up `ATTACHMENT_STORAGE_PATH` (default `var/attachments`)
separately. Stop the API and workers before restoring either data store, and
protect database backups, attachments, and notification logs as sensitive
health data.

Run `make test`, `make test-e2e`, `make coverage`, and `make audit` before a release. The
coverage target measures the security and authentication core and enforces
80%; dependency advisories are reported by `pip-audit` and must be reviewed
before release. With the demo family seeded, run `make benchmark-dashboard` to
measure 30 authorized local family-dashboard requests and enforce a p95 below
500 ms (override with `BENCHMARK_P95_TARGET_MS`). No fixed p95 result is
claimed by this README. Dashboard response caching is intentionally not
enabled: each request re-evaluates consent so revocations take effect
immediately. Query indexes and bounded route pagination are used without
caching health data across authorization changes.
