# -*- coding: utf-8 -*-
# Copyright (C) 2026 João Cardoso
# License: GNU General Public License v3 (see LICENSE)

"""Send to Calibre-web — Calibre interface action.

This module contains only backend-neutral orchestration: selecting books,
choosing a format, queueing one job per book, the per-batch circuit breaker,
session reuse, and the GUI callbacks. All server-specific work is delegated to
a Backend driver (see the ``backends`` package), so supporting another target
server is a matter of adding a driver, not touching this file.
"""

import os
import threading

from calibre.gui2.actions import InterfaceAction
from calibre.gui2 import error_dialog, info_dialog, question_dialog
try:
    from calibre.gui2.threaded_jobs import ThreadedJob
except ImportError:
    from calibre.gui2.jobs import ThreadedJob

from calibre_plugins.send_to_calibre_web.config import prefs
from calibre_plugins.send_to_calibre_web.backends import (
    get_backend_class, DEFAULT_BACKEND)
from calibre_plugins.send_to_calibre_web.backends.base import (
    BackendError, PermissionDeniedError)
import calibre_plugins.send_to_calibre_web.profiles as P

load_translations()


# --- Per-batch circuit breaker ----------------------------------------------
_server_down = threading.Event()

# Once a delete is refused for lack of permission (HTTP 403), replace can never
# succeed for this account, so the rest of the batch falls back to "keep"
# instead of attempting a doomed delete on every book.
_replace_disabled = threading.Event()


def reset_circuit_breaker():
    _server_down.clear()
    _replace_disabled.clear()


def is_connection_error(exc):
    """True for connection-level failures (server unreachable), as opposed to
    a BackendError (the server answered with an error)."""
    import urllib.error
    if isinstance(exc, BackendError):
        return False
    if isinstance(exc, urllib.error.HTTPError):
        return False
    return isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError))


# --- Backend construction with session reuse --------------------------------
_backend_lock = threading.Lock()
_backend_cache = {}


def _backend_cache_key(cfg, backend_key):
    return (backend_key, cfg['server_url'], cfg['username'],
            cfg['password'], cfg['verify_ssl'])


def get_connected_backend(backend_key, cfg, log=None):
    """Return a connected Backend instance, reusing a cached one when its
    session is still valid. Raises on connection/auth failure."""
    key = _backend_cache_key(cfg, backend_key)
    with _backend_lock:
        backend = _backend_cache.get(key)
    if backend is not None:
        backend._log = log
        try:
            backend.connect()
            return backend
        except Exception:
            with _backend_lock:
                _backend_cache.pop(key, None)
    cls = get_backend_class(backend_key)
    backend = cls(cfg, log=log)
    backend.connect()
    with _backend_lock:
        _backend_cache[key] = backend
    return backend


def cache_backend(backend_key, cfg, backend):
    with _backend_lock:
        _backend_cache[_backend_cache_key(cfg, backend_key)] = backend


# --- Format selection (Calibre-side, backend-neutral) -----------------------
def select_format(db, book_id, format_order):
    available = db.formats(book_id)
    if not available:
        return None, None
    available_upper = [f.upper() for f in available]
    for fmt in [f.strip().upper() for f in format_order.split(',')]:
        if fmt in available_upper:
            idx = available_upper.index(fmt)
            return available[idx], fmt
    return available[0], available[0].upper()


# --- Per-book job (backend-neutral) -----------------------------------------
def send_one_book_job(backend_key, cfg, title, authors, filepath, filename,
                      shelf_name, duplicate_policy, log, abort, notifications):
    """Send a single book via the selected backend; one ThreadedJob per book.

    ``duplicate_policy`` controls what happens when the book already exists on
    the server: 'keep' skips it (default); 'replace' deletes the existing copy
    then uploads the new one.
    """
    if _server_down.is_set():
        log('Server previously unreachable in this batch — skipping.')
        return 'skipped'

    try:
        notifications.put((0.1, _('Logging in…')))
        log(f'Book: {title}')
        backend = get_connected_backend(backend_key, cfg, log=log)

        if abort.is_set():
            log('Aborted.')
            return 'skipped'

        notifications.put((0.4, _('Checking for duplicate…')))
        result = 'skipped'
        exists = backend.book_exists(title, authors) \
            if backend.supports_duplicate_check else False

        # Decide the effective policy for this book. "replace" downgrades to
        # "keep" if replace was already disabled batch-wide by a prior 403.
        effective_policy = duplicate_policy
        if effective_policy == 'replace' and _replace_disabled.is_set():
            log('Replace disabled earlier this batch (no delete permission) — '
                'keeping existing.')
            effective_policy = 'keep'

        if exists and effective_policy != 'replace':
            # 'keep' (default): leave the existing copy untouched.
            log('Already exists on server, skipping upload.')
        elif exists and effective_policy == 'replace':
            # Try to delete the old copy, then upload. On failure, fall back to
            # keeping the existing copy (never lose what's already there).
            book_id = backend.find_book_id(title)
            if book_id is None:
                log('Exists but could not locate it to replace; keeping existing.')
            else:
                try:
                    notifications.put((0.5, _('Replacing existing…')))
                    log(f'Replacing existing copy (deleting book {book_id}).')
                    backend.delete_book(book_id)
                except PermissionDeniedError as e:
                    # Account-wide: disable replace for the rest of the batch.
                    _replace_disabled.set()
                    log(f'Cannot replace ({e}). Keeping existing, and falling '
                        f'back to "keep" for the rest of this batch.')
                    book_id = None  # signal: do not upload
                except BackendError as e:
                    # Per-book failure: keep this one, keep trying others.
                    log(f'Could not delete existing copy ({e}); keeping existing.')
                    book_id = None  # signal: do not upload
            if book_id is not None:
                if abort.is_set():
                    log('Aborted.')
                    return 'skipped'
                notifications.put((0.6, _('Uploading…')))
                status = backend.upload(filepath, filename)
                log(f'Sent OK ({status})')
                result = 'replaced'
        else:
            # New book: upload.
            if abort.is_set():
                log('Aborted.')
                return 'skipped'
            notifications.put((0.6, _('Uploading…')))
            status = backend.upload(filepath, filename)
            log(f'Sent OK ({status})')
            result = 'sent'
    except Exception as e:
        if is_connection_error(e):
            _server_down.set()
            log('Server unreachable (connection error) — '
                'stopping remaining sends in this batch.')
        raise

    if shelf_name and backend.supports_shelves:
        notifications.put((0.85, _('Adding to shelf…')))
        try:
            book_id = backend.find_book_id(title)
            if book_id is None:
                log(f'Warning: book not found on server, cannot add to shelf "{shelf_name}".')
            else:
                shelf_id = backend.ensure_shelf(shelf_name)
                outcome = backend.add_to_shelf(shelf_id, book_id)
                if outcome == 'already':
                    log(f'Already on shelf "{shelf_name}".')
                else:
                    log(f'Added to shelf "{shelf_name}".')
        except Exception as e:
            log(f'Warning: could not add to shelf "{shelf_name}": {e}')

    return result


class SendToCalibreWebAction(InterfaceAction):

    name = 'Send to Calibre-web'
    action_spec = (_('Send to Calibre-web'), None,
                   _('Send selected books to a Calibre-web server'), None)
    action_type = 'current'
    action_add_menu = True
    dont_add_to = frozenset(['context-menu-device'])

    def genesis(self):
        icon = get_icons('images/icon.png', 'Send to Calibre-web')
        self.qaction.setIcon(icon)
        self.qaction.triggered.connect(self.send_to_default)
        # Calibre auto-creates the menu (action_add_menu = True); we populate
        # it fresh each time it is about to show so it reflects current profiles.
        self.menu = self.qaction.menu()
        self.menu.aboutToShow.connect(self._build_menu)

    def _build_menu(self):
        from qt.core import QMenu
        self.menu.clear()
        P.migrate(prefs)
        profiles = P.get_profiles(prefs)
        active = prefs.get('active_profile')

        # Profile names — clicking one sends to it as a one-off.
        for p in profiles:
            name = p['name']
            act = self.menu.addAction(
                ('✓ ' if name == active else '    ') + name)
            act.triggered.connect(lambda checked=False, n=name: self.send_to_profile(n))

        self.menu.addSeparator()
        # Submenu to change the default without sending.
        sub = QMenu(_('Set default profile'), self.menu)
        for p in profiles:
            name = p['name']
            a = sub.addAction(('✓ ' if name == active else '    ') + name)
            a.triggered.connect(lambda checked=False, n=name: self._set_default(n))
        self.menu.addMenu(sub)

        self.menu.addSeparator()
        cfg_act = self.menu.addAction(_('Configure profiles…'))
        cfg_act.triggered.connect(self._open_config)

    def _set_default(self, name):
        P.set_active_profile(prefs, name)
        self.gui.status_bar.show_message(
            _('Default profile set to %s') % name, 3000)

    def _open_config(self):
        self.interface_action_base_plugin.do_user_config(self.gui)

    def send_to_default(self):
        prof = P.get_active_profile(prefs)
        if prof is None:
            return error_dialog(self.gui, _('No profile configured'),
                                _('Please add a profile in the plugin settings.'), show=True)
        self._send_with_profile(prof)

    def send_to_profile(self, name):
        prof = P.get_profile(prefs, name)
        if prof is None:
            return error_dialog(self.gui, _('Unknown profile'),
                                _('Profile "%s" no longer exists.') % name, show=True)
        self._send_with_profile(prof)

    def send_books(self):
        # Back-compat entry point: send to the active profile.
        self.send_to_default()

    def _send_with_profile(self, profile):
        rows = self.gui.library_view.selectionModel().selectedRows()
        if not rows:
            return error_dialog(self.gui, _('No books selected'),
                                _('Please select one or more books first.'), show=True)

        cfg = P.profile_to_config(profile)
        backend_key = profile.get('backend', DEFAULT_BACKEND)
        format_order = profile.get('format_order', 'epub,mobi,azw3,fb2,pdf')

        if not cfg['server_url']:
            return error_dialog(self.gui, _('Not configured'),
                                _('Profile "%s" has no server URL.') % profile['name'], show=True)

        db = self.gui.current_db.new_api
        book_ids = [self.gui.library_view.model().id(row) for row in rows]

        book_data = []
        missing_format = []
        for book_id in book_ids:
            mi = db.get_metadata(book_id)
            fmt, fmt_upper = select_format(db, book_id, format_order)
            if fmt is None:
                missing_format.append(mi.title)
                continue
            filepath = db.format_abspath(book_id, fmt_upper)
            if not filepath or not os.path.exists(filepath):
                missing_format.append(mi.title)
                continue
            filename = os.path.basename(filepath)
            authors = mi.authors or ['Unknown']
            book_data.append((book_id, mi.title, authors, filepath, filename))

        if missing_format:
            names = '\n'.join(missing_format)
            if not question_dialog(self.gui, _('Missing formats'),
                    _('The following books have no sendable format and will be skipped:') + f'\n{names}\n\n' + _('Continue?')):
                return

        if not book_data:
            return info_dialog(self.gui, _('Nothing to send'),
                               _('No books with a suitable format were found.'), show=True)

        shelf_name = ''
        if profile.get('add_to_shelf'):
            shelf_name = (profile.get('shelf_name') or '').strip()
            if not shelf_name:
                shelf_name = os.path.basename(
                    os.path.normpath(self.gui.current_db.library_path))

        duplicate_policy = profile.get('duplicate_policy', 'keep')

        # Pre-flight: connect once, up front.
        reset_circuit_breaker()
        try:
            backend = get_connected_backend(backend_key, cfg, log=None)
            cache_backend(backend_key, cfg, backend)
        except BackendError as e:
            return error_dialog(self.gui, _('Login failed'),
                                _('Could not log in to "%s":') % profile['name'] + f'\n\n{e}', show=True)
        except Exception as e:
            return error_dialog(self.gui, _('Cannot reach server'),
                                _('Could not connect to "%s":') % profile['name'] + f'\n\n{e}', show=True)

        # If a saved profile asks for "replace"/"ask" but the backend can't
        # delete, downgrade to "keep" silently (the job logs it).
        if duplicate_policy in ('replace', 'ask') and not getattr(backend, 'supports_replace', False):
            duplicate_policy = 'keep'

        # "Always ask": one dialog up front decides whether duplicates are
        # replaced or kept for this send. The per-book duplicate check during
        # the send then applies that choice to whichever books actually exist.
        if duplicate_policy == 'ask':
            answer = question_dialog(
                self.gui, _('Replace existing books?'),
                _('If any of the selected books already exist on "%s", '
                  'replace them?') % profile['name'] + '\n\n' +
                _('Yes — replace duplicates (delete the existing copy then '
                  'upload).\nNo — keep the existing copies and skip those.'),
                show_copy_button=False)
            duplicate_policy = 'replace' if answer else 'keep'

        for book_id, title, authors, filepath, filename in book_data:
            job = ThreadedJob(
                'send_to_calibre_web',
                _('Sending "%s" to %s') % (title, profile['name']),
                send_one_book_job,
                (backend_key, cfg, title, authors, filepath, filename,
                 shelf_name, duplicate_policy),
                {},
                self.send_complete
            )
            self.gui.job_manager.run_threaded_job(job)
        self.gui.status_bar.show_message(
            _('Queued %d book(s) for %s') % (len(book_data), profile['name']), 3000)

    def send_complete(self, job):
        try:
            desc = getattr(job, 'description', '') or _('Send to Calibre-web')
            if getattr(job, 'failed', False):
                self.gui.status_bar.show_message(desc + ' — ' + _('failed'), 5000)
            else:
                self.gui.status_bar.show_message(desc + ' — ' + _('done'), 3000)
        except Exception:
            pass
