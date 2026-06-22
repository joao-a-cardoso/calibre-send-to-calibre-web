# -*- coding: utf-8 -*-
# Copyright (C) 2026 João Cardoso
# License: GNU General Public License v3 (see LICENSE)

"""Backend driver registry.

To add a new target server, implement a Backend subclass in this package and
register it in BACKENDS below. Nothing else in the plugin needs to change.
"""

from calibre_plugins.send_to_calibre_web.backends.base import Backend, BackendError
from calibre_plugins.send_to_calibre_web.backends.calibre_web import CalibreWebBackend

# Re-exported for convenience so callers can do
# `from ...backends import Backend, BackendError`.
__all__ = [
    'Backend', 'BackendError', 'CalibreWebBackend',
    'BACKENDS', 'DEFAULT_BACKEND', 'get_backend_class', 'backend_choices',
]

#: key -> Backend subclass
BACKENDS = {
    CalibreWebBackend.key: CalibreWebBackend,
}

#: The default backend key (current behavior).
DEFAULT_BACKEND = CalibreWebBackend.key


def get_backend_class(key):
    """Return the Backend subclass for ``key``, falling back to the default."""
    return BACKENDS.get(key, BACKENDS[DEFAULT_BACKEND])


def backend_choices():
    """Return [(key, name), ...] for populating a settings dropdown."""
    return [(k, cls.name) for k, cls in BACKENDS.items()]
