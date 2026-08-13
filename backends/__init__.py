# -*- coding: utf-8 -*-
# Copyright (C) 2026 João Cardoso
# License: GNU General Public License v3 (see LICENSE)

"""Backend driver registry.

To add a new target server, implement a Backend subclass in this package and
register it in BACKENDS below. Nothing else in the plugin needs to change.
"""

from calibre_plugins.send_to_calibre_web.backends.base import (
    AuthenticationError, Backend, BackendError, BookIdentity,
    DuplicateLookupError, LookupResult, LookupStatus, PermissionDeniedError,
    ProtocolError, RemoteBook, SessionExpiredError,
)
from calibre_plugins.send_to_calibre_web.backends.calibre_web import CalibreWebBackend

__all__ = [
    'Backend', 'BackendError', 'AuthenticationError', 'SessionExpiredError',
    'PermissionDeniedError', 'ProtocolError', 'DuplicateLookupError',
    'BookIdentity', 'RemoteBook', 'LookupResult', 'LookupStatus',
    'CalibreWebBackend', 'BACKENDS', 'DEFAULT_BACKEND', 'get_backend_class',
    'backend_choices',
]

BACKENDS = {
    CalibreWebBackend.key: CalibreWebBackend,
}

DEFAULT_BACKEND = CalibreWebBackend.key


def get_backend_class(key):
    return BACKENDS.get(key, BACKENDS[DEFAULT_BACKEND])


def backend_choices():
    return [(k, cls.name) for k, cls in BACKENDS.items()]
