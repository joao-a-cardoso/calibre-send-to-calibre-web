# BookOrbit driver — verification checklist

`backends/bookorbit.py` is a working skeleton against the `Backend` interface.
Three things need confirming against a real BookOrbit instance before it can be
enabled. Spin up BookOrbit (Podman/Docker), open the browser DevTools **Network**
tab (tick **Preserve log**), perform each action, and for each request use
**right-click → Copy → Copy as cURL**. Paste those and the driver can be finished.

## 1. Login → JWT  (marked `# VERIFY (1)` in bookorbit.py)

Log in and capture the auth request.

- [ ] Endpoint path and method (skeleton assumes `POST /api/auth/login`)
- [ ] Request body: JSON or form? Field names — `username` or `email`?
- [ ] Where the token comes back: response JSON field name (`token`,
      `access_token`, `accessToken`, `jwt`?) **or** a cookie
- [ ] How it's sent on later requests: `Authorization: Bearer <token>` header,
      or cookie

## 2. Upload a book  (marked `# VERIFY (2)`)

Drag a book into the upload dialog and capture the upload request.

- [ ] Endpoint path (scoped to a library? e.g.
      `POST /api/libraries/<id>/...` vs a flat `/api/upload`)
- [ ] Is it `multipart/form-data`? The **file field name** (skeleton uses `file`)
- [ ] Any extra form fields (target library id, etc.)
- [ ] Success response: status and body (does it return the new book id?)

## 3. Error responses  (marked `# VERIFY (3)`)

Capture these so failures report a clear reason instead of a raw HTTP error:

- [ ] Upload a **duplicate** filename (already on disk) → status + body
- [ ] Upload a **disallowed format** (not in the library's allowed list) → status + body
- [ ] An unauthenticated upload (no/expired token) → status

## 4. OPDS search (optional but nice — `book_exists`)

- [ ] The OPDS search URL and query parameter (skeleton assumes
      `/opds/search?query=<title>`)
- [ ] Does OPDS need the bearer token, basic auth, or nothing?
- [ ] One `<entry>` from the feed (to confirm title parsing)

## 5. Collections / Smart Scopes (only if you want shelf support)

`supports_shelves` is currently `False`. To enable it, capture:

- [ ] List collections, create a collection, add a book to a collection —
      endpoints + request bodies
- [ ] Then implement `find_book_id`, `ensure_shelf`, `add_to_shelf` and flip
      `supports_shelves = True`

## Enabling the driver

Once 1–3 are confirmed and filled in, register it in
`backends/__init__.py`:

```python
from calibre_plugins.send_to_calibre_web.backends.bookorbit import BookOrbitBackend

BACKENDS = {
    CalibreWebBackend.key: CalibreWebBackend,
    BookOrbitBackend.key: BookOrbitBackend,
}
```

It will then appear in the settings "Backend" dropdown automatically.
