# Send to Calibre-web

A [Calibre](https://calibre-ebook.com/) plugin that sends selected books from your
Calibre library to a [Calibre-web](https://github.com/janeczku/calibre-web) server,
with duplicate detection and optional shelf assignment.

Each book is sent as its own background job, so you can keep working while uploads
run and abort individual books if needed.

## Features

- **Send selected books** to a Calibre-web server over HTTP or HTTPS.
- **Session login with CSRF** — authenticates the same way the web UI does, so the
  `/upload` endpoint actually accepts the books (Basic auth alone does not work for
  uploads in Calibre-web).
- **Duplicate detection** — queries the server's OPDS catalog and skips books whose
  title already exists, so re-sending a selection is safe.
- **Per-book jobs** — one Calibre job per book, each individually logged and
  abortable from the jobs panel.
- **Session reuse** — a single login is shared across all the jobs in a batch, and
  re-established automatically if it expires.
- **Shelf assignment** — optionally add every sent book to a Calibre-web shelf,
  creating the shelf if it does not exist. Leave the shelf name empty to use the
  name of the currently open Calibre library. Books skipped as duplicates are still
  added to the shelf, so you can re-send a whole library purely to assign shelves.
- **Format preference** — choose which format is uploaded when a book has several.
- **Self-signed HTTPS** — an option to skip certificate verification for servers
  using self-signed certificates.
- **Test connection** button in the settings dialog.
- **Translatable**, with a Portuguese (pt_PT) translation included.

## Requirements

- Calibre 6.0 or newer.
- A reachable Calibre-web server with **uploads enabled**
  (*Admin → Basic Configuration → Feature Configuration → Enable Uploads*).
- A Calibre-web user account with permission to upload and (for shelves) to edit
  public shelves.

## Installation

From a release zip:

1. In Calibre, go to **Preferences → Plugins → Load plugin from file**.
2. Select `send-to-calibre-web.zip`.
3. Confirm the security prompt and **restart Calibre**.

From source, see [Building](#building).

## Configuration

Open **Preferences → Plugins → Send to Calibre-web → Customize plugin** and set:

| Setting | Description |
|---|---|
| Server URL | Base URL of your Calibre-web server, e.g. `http://192.168.1.50:8083`. |
| Username / Password | A Calibre-web account with upload permission. |
| Verify SSL certificate | Untick for self-signed HTTPS servers. |
| Format preference | Comma-separated order, e.g. `epub,mobi,azw3,fb2,pdf`. |
| Add sent books to a shelf | Enable shelf assignment. |
| Shelf name | Target shelf; empty uses the current library name. |

Use **Test connection** to confirm the server, credentials and TLS settings before
sending.

## Usage

1. Select one or more books in your Calibre library.
2. Click the **Send to Calibre-web** toolbar button.
3. Watch the jobs panel — one job per book. Each job logs login, duplicate check,
   upload and (if enabled) shelf assignment.

## Building

The plugin is a plain zip with the source files at the root. Use the build script:

```bash
./build.sh            # produces send-to-calibre-web.zip
./build.sh --install  # build and install via calibre-customize
./build.sh --clean    # remove build artifacts
```

`build.sh` compiles any `translations/*.po` into `.mo` files (if `msgfmt` is
available) and bundles them.

## Translations

- `translations/send_to_calibre_web.pot` — message template.
- `translations/pt.po` — Portuguese (Portugal).

To add a language, copy the `.pot` to `translations/<lang>.po`, translate the
`msgstr` entries, and rebuild.

## How it works

Calibre-web's upload form requires a logged-in session and a CSRF token, so the
plugin:

1. Fetches `/login`, extracts the `csrf_token`, and posts the credentials to obtain
   a session cookie.
2. Checks `/opds/search?query=<title>` (Basic auth) to detect duplicates.
3. Posts the book to `/upload` as `multipart/form-data` with the session cookie and
   CSRF token, verifying it was not bounced back to the login page.
4. If shelf assignment is enabled, looks up or creates the shelf via
   `/opds/shelfindex` and `/shelf/create`, then calls
   `/shelf/add/<shelf_id>/<book_id>`.

## License

GNU General Public License v3.0 or later. See [LICENSE](LICENSE).

## Authors

João Cardoso with Claude (Anthropic)
