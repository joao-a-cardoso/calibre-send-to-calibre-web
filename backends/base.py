# -*- coding: utf-8 -*-
# Copyright (C) 2026 João Cardoso
# License: GNU General Public License v3 (see LICENSE)

"""Backend driver interface for Send to Calibre-web.

A backend encapsulates everything server-specific: authentication, duplicate
detection, upload, and (optionally) shelf assignment. The job orchestration in
action.py is backend-neutral and talks only to this interface, so adding a new
target server (e.g. BookOrbit) means writing a new Backend subclass — no
changes to the job logic.

Capability flags let the orchestration skip operations a backend does not
support (e.g. a folder-scan backend with no shelves) without special-casing.
"""


class BackendError(Exception):
    """Raised by a backend for a genuine, per-item failure (the server
    answered with an error). Connection-level problems should propagate as
    the original TimeoutError/ConnectionError/URLError so the orchestration
    can trip its circuit breaker."""


class PermissionDeniedError(BackendError):
    """Raised when the server refuses an operation because the account lacks
    permission (HTTP 403). Distinguished from a generic BackendError so the
    orchestration can react account-wide (e.g. disable Replace for the whole
    batch rather than retrying a doomed delete on every book)."""


class Backend:
    """Abstract base for a target-server driver.

    Subclasses identify themselves with a unique ``key`` and human-readable
    ``name``, declare their capabilities, and implement the operations below.

    A backend instance is constructed with its configuration (server URL,
    credentials, etc.) and holds any session/opener it needs internally. The
    orchestration never sees cookies, tokens, or URLs directly.
    """

    #: Unique identifier used in config and the backend registry.
    key = ''
    #: Human-readable name shown in the settings dropdown.
    name = ''

    # --- Capability flags (override in subclasses as needed) ---
    supports_duplicate_check = True
    supports_shelves = True
    #: Whether the backend can delete an existing book (needed for the
    #: "Replace existing" duplicate policy).
    supports_replace = True

    def __init__(self, config, log=None):
        """``config`` is a dict of backend-specific settings; ``log`` is an
        optional callable for progress/diagnostic lines."""
        self.config = config
        self._log = log

    def log(self, msg):
        if self._log:
            self._log(msg)

    # --- Lifecycle ---
    def connect(self):
        """Establish/validate a session. Raise on failure. Connection-level
        errors should propagate unchanged; auth failures should raise
        BackendError."""
        raise NotImplementedError

    def test_connection(self):
        """Return (ok: bool, message: str) for the settings 'Test connection'
        button. Must not raise."""
        raise NotImplementedError

    # --- Core operations ---
    def book_exists(self, title, authors):
        """Return True if a book with this title already exists on the server.
        Should fail open (return False) on transient errors."""
        raise NotImplementedError

    def upload(self, filepath, filename):
        """Upload the file. Return a short status string on success; raise
        BackendError (or let a connection error propagate) on failure."""
        raise NotImplementedError

    def delete_book(self, book_id):
        """Delete a book by its server-side id (used by the 'Replace existing'
        policy). Return None on success; raise BackendError on failure.
        Only called when supports_replace is True."""
        raise NotImplementedError

    # --- Shelves (only called when supports_shelves is True) ---
    def find_book_id(self, title):
        """Return the server-side id for an exact title match, or None."""
        raise NotImplementedError

    def ensure_shelf(self, shelf_name):
        """Return the id of the named shelf, creating it if needed."""
        raise NotImplementedError

    def add_to_shelf(self, shelf_id, book_id):
        """Add a book to a shelf. Return 'added' or 'already'; raise
        BackendError on a genuine failure."""
        raise NotImplementedError
