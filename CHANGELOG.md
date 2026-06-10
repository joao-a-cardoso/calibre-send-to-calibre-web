# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
