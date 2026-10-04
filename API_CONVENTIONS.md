# API conventions

- The frontend and API share one origin: FastAPI serves `frontend/` at `/` and
  the versioned API at `/api/v1`. Browser requests use relative URLs; no
  frontend development-server proxy or cross-origin credentials are needed.
- JSON property names use `snake_case`. Resource identifiers are UUIDs in path
  segments. Dates use ISO 8601; timestamps include a timezone.
- Successful responses use the endpoint's documented JSON shape. Errors use
  `{"error":{"code":"...","message":"...","details":[...]}}`; `details` is
  omitted when there is no structured detail.
- Auth uses the server-managed HttpOnly session cookie. Obtain a CSRF challenge
  from `GET /api/v1/auth/csrf`; send its value in `X-CSRF-Token` for
  state-changing requests. Login and registration return the rotated CSRF token.
- Profiles are listed from `GET /api/v1/profiles`; the
  `PATCH /api/v1/profiles/{profile_id}` endpoint saves onboarding changes
  incrementally. The profile response includes onboarding progress,
  relationship, and persisted avatar colors. Dependent profiles are created
  with `POST /api/v1/profiles` and a family ID. Non-auth frontend API requests
  include the active `profile_id`; profile-scoped endpoints still use the
  profile UUID in the path as their authorization boundary.
- Frontend/API contract changes are reviewed against the generated
  `openapi/auth-profiles.openapi.json`. Regenerate it with `make openapi`; the
  generated Postman collection is `postman/auth-profiles.postman_collection.json`
  (`make postman-contract`).
- CORS remains limited to `http://localhost` for development. Same-origin
  frontend requests do not require CORS.
