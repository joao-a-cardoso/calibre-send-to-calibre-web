# -*- coding: utf-8 -*-
# Copyright (C) 2026 João Cardoso
# License: GNU General Public License v3 (see LICENSE)

"""Backend driver interface for Send to Calibre-web.

A backend encapsulates everything server-specific: authentication, remote-book
lookup, upload, deletion, and (optionally) shelf assignment.  The orchestration
in action.py is backend-neutral and talks only to this interface.

Remote lookup is deliberately richer than a boolean duplicate check.  A lookup
can be FOUND, NOT_FOUND, UNKNOWN (the query failed), or AMBIGUOUS (more than one
remote book could be the target).  Destructive operations must only use a
resolved RemoteBook id.
"""

from dataclasses import dataclass, field
from enum import Enum


class BackendError(Exception):
    """Base class for server/protocol failures.

    Connection-level problems should normally propagate as their original
    TimeoutError/ConnectionError/URLError so orchestration can trip its
    per-batch server-down circuit breaker.
    """


class AuthenticationError(BackendError):
    """Authentication failed or a session could not be re-established."""


class SessionExpiredError(AuthenticationError):
    """Internal signal that an authenticated session has expired."""


class PermissionDeniedError(BackendError):
    """The authenticated account lacks permission for an operation (HTTP 403)."""


class ProtocolError(BackendError):
    """The server answered, but the response did not match the expected protocol."""


class DuplicateLookupError(BackendError):
    """Remote-book lookup could not be completed reliably."""


class LookupStatus(Enum):
    FOUND = 'found'
    NOT_FOUND = 'not_found'
    UNKNOWN = 'unknown'
    AMBIGUOUS = 'ambiguous'


@dataclass(frozen=True)
class BookIdentity:
    title: str
    authors: tuple = ()
    identifiers: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RemoteBook:
    id: object
    title: str
    authors: tuple = ()
    identifiers: dict = field(default_factory=dict)


@dataclass(frozen=True)
class LookupResult:
    status: LookupStatus
    book: object = None
    detail: str = ''

    @classmethod
    def found(cls, book):
        return cls(LookupStatus.FOUND, book=book)

    @classmethod
    def not_found(cls, detail=''):
        return cls(LookupStatus.NOT_FOUND, detail=detail)

    @classmethod
    def unknown(cls, detail=''):
        return cls(LookupStatus.UNKNOWN, detail=detail)

    @classmethod
    def ambiguous(cls, detail=''):
        return cls(LookupStatus.AMBIGUOUS, detail=detail)


class Backend:
    """Abstract base for a target-server driver."""

    key = ''
    name = ''

    supports_duplicate_check = True
    supports_shelves = True
    # Standalone removal of a resolved remote book.  A backend may support
    # duplicate lookup/upload without exposing a delete operation to users.
    supports_delete = False
    supports_replace = True

    def __init__(self, config, log=None, shared_state=None):
        self.config = config
        self._log = log
        self._shared_state = shared_state

    @property
    def shared_state(self):
        """Opaque state that may be reused by sibling backend instances.

        Per-job state such as the logger must not be placed here.  Backends may
        use this for authenticated sessions, connection pools, etc.
        """
        return self._shared_state

    def log(self, msg):
        if self._log:
            self._log(msg)

    # --- lifecycle ---------------------------------------------------------
    def connect(self, validate=False):
        raise NotImplementedError

    def test_connection(self):
        raise NotImplementedError

    # --- core operations ---------------------------------------------------
    def find_book(self, identity):
        """Resolve a local BookIdentity to one remote book.

        Return LookupResult.  UNKNOWN means the lookup itself failed; it must
        not be treated as NOT_FOUND.  AMBIGUOUS means multiple plausible remote
        books exist and no destructive operation should be attempted.
        """
        raise NotImplementedError

    def upload(self, filepath, filename):
        raise NotImplementedError

    def delete_book(self, book_id):
        raise NotImplementedError

    # --- shelves -----------------------------------------------------------
    def ensure_shelf(self, shelf_name):
        raise NotImplementedError

    def add_to_shelf(self, shelf_id, book_id):
        raise NotImplementedError
