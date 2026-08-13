# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

# [1.6.0] - 2026-08-13

### Changed
Hardened batch isolation, session handling, duplicate matching, and replacement safety.

## [1.5.0] - 2026-06-28

### Added
- Per-profile duplicate handling: when a book already exists on the server,
  choose **Keep existing** (skip — the previous behaviour, still the default),
  **Replace existing** (delete the existing copy, then upload the new one), or
  **Always ask before replacing** (a single prompt before sending decides
  whether duplicates are replaced or kept for that send).
- The "Replace" and "Always ask" options are only offered for backends that
  support deletion (a `supports_replace` capability on each driver). Calibre-web
  supports it; the BookOrbit skeleton does not.

### Changed
- If a "Replace" cannot delete the existing copy, it falls back to keeping it
  rather than failing — the existing copy is never lost. A permission error
  (HTTP 403, e.g. the account lacks the Calibre-web "Delete books" permission)
  disables Replace for the rest of the batch; other delete errors fall back for
  just that book. All fallbacks are recorded in the job log.

## [1.4.0] - 2026-06-10

### Added
- Connection profiles: define several named server configurations and switch
  between them. Each profile is self-contained — its own backend, server,
  credentials, format preference, and shelf settings.
- The settings dialog is now a two-pane profile manager (list of profiles with
  Add / Remove / Rename / Duplicate, editing the selected profile on the right)
  with a "Set as default" button.
- The toolbar button gained a dropdown menu: click the icon to send to the
  default profile, or use the menu to send to a specific profile (one-off),
  change the default profile, or open the settings.
- A "Test connection" button that uses the selected backend's own check.

### Changed
- Refactored to a multi-backend driver architecture. All server-specific work
  (login, duplicate check, upload, shelves) now lives behind a `Backend`
  interface in a new `backends` package, with Calibre-web as the first driver.
  The job orchestration (per-book jobs, circuit breaker, session reuse) is now
  backend-neutral, so supporting another target server is a matter of adding a
  driver.
- Existing single-server settings are migrated automatically into one profile
  named "Default" on first run; no settings are lost.
- The toolbar menu now uses Calibre's idiomatic `action_add_menu` mechanism.
- Removed dead code, made backend re-exports explicit (`__all__`), refreshed the
  Portuguese translation to cover all UI strings, and documented that profile
  passwords are stored in plain text (a Calibre configuration limitation).

## [1.3.4] - 2026-06-10

### Changed
- Widened the settings dialog and gave all input fields a uniform width, so
  the server URL and format list are no longer truncated.

## [1.3.3] - 2026-06-10

### Fixed
- Connection-level failures (timeout, refused, DNS) now trip a per-batch
  circuit breaker: once the server is found unreachable, the remaining queued
  books skip immediately instead of each waiting out its own timeout. Per-book
  HTTP errors do not trip it — those books fail individually and the batch
  continues.
- The successful pre-flight login now seeds the shared session cache, so the
  per-book jobs reuse it instead of logging in again.

## [1.3.2] - 2026-06-10

### Fixed
- The job-completion callback no longer opens a modal error dialog. A single
  failed send (e.g. wrong password) could crash Calibre — and a whole failed
  batch would stack hundreds of modal dialogs. Failures now show as a brief
  status-bar message and remain visible, with full logs, in the jobs panel.
- Credentials and server reachability are now validated once, up front, before
  any jobs are queued: a wrong password or unreachable server produces a single
  clear dialog instead of one failed job per book.
- Adding a book to a shelf no longer crashes the job when the book is already
  on that shelf: Calibre-web returns HTTP 400 ("Book is already part of the
  shelf") in that case, which is now treated as a benign result. This happened
  when re-sending duplicates that had been shelved on a previous run.
- The `/shelf/add` request now matches Calibre-web's AJAX contract (empty body,
  `X-Requested-With` and `X-CSRFToken` headers), avoiding spurious 400s.
- Any shelf-assignment failure (permissions, invalid book id, etc.) is now
  caught and logged as a warning instead of failing the whole job — the book
  upload itself already succeeded.
- Fixed a crash when a send failed and the error dialog's "Show details" was
  opened: details are now built from the job's exception traceback instead of
  the not-yet-ready consolidated log, avoiding format-character and threading
  issues.
- Upload errors are now diagnosable: HTTP 4xx/5xx responses (including 422 for
  disallowed formats or disabled uploads) are caught and reported with the
  server's flash message instead of crashing or showing a raw urllib error.
- Added the missing `urllib.error` import that could itself raise on any
  server-side HTTP error.

## [1.3.1] - 2026-06-10

### Changed
- Revised authorship and copyright attribution.
- Bumped version to 1.3.1.

## [1.3.0] - 2026-06-10

### Added
- Optional shelf assignment: sent books can be added to a Calibre-web shelf,
  created automatically if missing. An empty shelf name uses the current Calibre
  library name. Duplicates are shelved too.
- Session reuse across per-book jobs, with automatic re-login on expiry.
- "Test connection" button in the settings dialog.
- Portuguese (pt_PT) translation and a `.pot` template.
- `build.sh` build script and a git repository layout.

### Changed
- Each book is now sent as its own background job instead of one batch job, so
  individual books can be logged and aborted separately.

### Fixed
- Uploads now perform a real Calibre-web session login with CSRF token; previously
  Basic auth was used, which Calibre-web redirected to the login page while the
  plugin reported false success and nothing was stored.
- Duplicate detection now uses the correct OPDS `query` parameter (was `q`), so
  existing books are properly skipped.
- Multipart filenames are sanitized (quotes and newlines) to avoid corrupting the
  upload body.

## [1.2.0] - prior

### Added
- Initial implementation: ThreadedJob upload, OPDS duplicate check, multipart
  upload, self-signed HTTPS support, format selector, and plugin icon.
