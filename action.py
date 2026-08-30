# -*- coding: utf-8 -*-
# Copyright (C) 2026 João Cardoso
# License: GNU General Public License v3 (see LICENSE)

"""Send to Calibre-web — Calibre interface action.

This module contains only backend-neutral orchestration: selecting books,
choosing a format, queueing one job per book, per-send circuit breakers,
shared-session reuse, and GUI callbacks. Server-specific work is delegated to a
Backend driver.
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
    AuthenticationError, BackendError, BookIdentity, DEFAULT_BACKEND,
    LookupStatus, PermissionDeniedError, get_backend_class,
)
import calibre_plugins.send_to_calibre_web.profiles as P

load_translations()


class SendBatchContext:
    """Mutable state shared only by jobs belonging to one send operation."""

    def __init__(self):
        self.server_down = threading.Event()
        self.replace_disabled = threading.Event()
        self.delete_disabled = threading.Event()


# --- Shared authenticated state cache --------------------------------------
# Cache only a backend's opaque shared state (e.g. CookieJar/opener manager),
# never a whole backend instance or its per-job logger.
_shared_state_lock = threading.Lock()
_shared_state_cache = {}


def _shared_state_cache_key(backend_key, profile_id, connection_revision):
    return backend_key, profile_id, int(connection_revision or 0)


def _purge_stale_profile_state(backend_key, profile_id, keep_key):
    stale = [
        key for key in _shared_state_cache
        if key[0] == backend_key and key[1] == profile_id and key != keep_key
    ]
    for key in stale:
        _shared_state_cache.pop(key, None)


def get_connected_backend(backend_key, cfg, session_key, log=None, validate=False):
    """Return a fresh per-job backend wrapper using cached shared session state."""
    key = _shared_state_cache_key(backend_key, *session_key)
    with _shared_state_lock:
        shared_state = _shared_state_cache.get(key)

    cls = get_backend_class(backend_key)
    backend = cls(cfg, log=log, shared_state=shared_state)
    backend.connect(validate=validate)

    with _shared_state_lock:
        _shared_state_cache[key] = backend.shared_state
        _purge_stale_profile_state(backend_key, session_key[0], key)
    return backend




def _prune_shared_state_cache(profiles):
    """Drop cached state for profiles that no longer exist."""
    live_ids = {p.get('id') for p in profiles if p.get('id')}
    with _shared_state_lock:
        stale = [key for key in _shared_state_cache if key[1] not in live_ids]
        for key in stale:
            _shared_state_cache.pop(key, None)


def is_connection_error(exc):
    """True for server-unreachable failures, not server/protocol responses."""
    import urllib.error
    if isinstance(exc, BackendError):
        return False
    if isinstance(exc, urllib.error.HTTPError):
        return False
    return isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError))


# --- Format selection -------------------------------------------------------
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


def _lookup_or_skip(backend, identity, log):
    """Return LookupResult and log conservative failures consistently."""
    lookup = backend.find_book(identity)
    if lookup.status == LookupStatus.UNKNOWN:
        suffix = f' ({lookup.detail})' if lookup.detail else ''
        log('Could not verify whether this book already exists%s; skipping for safety.' % suffix)
    elif lookup.status == LookupStatus.AMBIGUOUS:
        suffix = f' ({lookup.detail})' if lookup.detail else ''
        log('Remote match is ambiguous%s; skipping for safety.' % suffix)
    return lookup


def _resolved_book_for_shelf(backend, identity, known_book, log):
    if known_book is not None and known_book.id is not None:
        return known_book
    # We just uploaded this book successfully, so it must exist on the server
    # even if OPDS search doesn't reflect it instantly (Calibre-web can take a
    # moment to index a newly-uploaded book before it's searchable). Retry a
    # few times with a short delay before giving up.
    import time
    delays = (0, 1, 2, 4)  # seconds; first attempt has no delay
    lookup = None
    for i, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        lookup = backend.find_book(identity)
        if lookup.status == LookupStatus.FOUND and lookup.book and lookup.book.id is not None:
            if i > 0:
                log(f'Found on server after {i} retr{"y" if i == 1 else "ies"} '
                    f'(OPDS indexing lag).')
            return lookup.book
        if lookup.status == LookupStatus.AMBIGUOUS:
            # Ambiguity won't resolve itself by waiting — stop retrying.
            break
    if lookup.status == LookupStatus.AMBIGUOUS:
        log('Warning: uploaded book could not be identified uniquely for shelf assignment.')
    elif lookup.status == LookupStatus.UNKNOWN:
        log('Warning: uploaded book could not be looked up for shelf assignment.')
    else:
        log('Warning: uploaded book was not found for shelf assignment '
            '(server search may still be indexing it — try again shortly).')
    return None


# --- Per-book job -----------------------------------------------------------
def send_one_book_job(backend_key, cfg, session_key, batch, identity, filepath,
                      filename, shelf_name, duplicate_policy, log, abort,
                      notifications):
    """Send one book. One ThreadedJob invokes this function per selected book."""
    if batch.server_down.is_set():
        log('Server previously unreachable in this send — skipping.')
        return 'skipped'

    backend = None
    result = 'skipped'
    remote_for_shelf = None

    try:
        notifications.put((0.1, _('Logging in…')))
        log(f'Book: {identity.title}')
        backend = get_connected_backend(backend_key, cfg, session_key, log=log)

        if abort.is_set():
            log('Aborted.')
            return 'skipped'

        lookup = None
        if backend.supports_duplicate_check:
            notifications.put((0.4, _('Checking for duplicate…')))
            lookup = _lookup_or_skip(backend, identity, log)
            if lookup.status in (LookupStatus.UNKNOWN, LookupStatus.AMBIGUOUS):
                return 'skipped'
        else:
            # A backend without duplicate lookup simply proceeds as a new upload.
            lookup = None

        exists = lookup is not None and lookup.status == LookupStatus.FOUND
        if exists:
            remote_for_shelf = lookup.book

        effective_policy = duplicate_policy
        if effective_policy == 'replace' and batch.replace_disabled.is_set():
            log('Replace disabled earlier in this send (no delete permission) — '
                'keeping existing.')
            effective_policy = 'keep'

        if exists and effective_policy != 'replace':
            log('Already exists on server, skipping upload.')

        elif exists and effective_policy == 'replace':
            remote = lookup.book
            if remote is None or remote.id is None:
                log('Existing book has no resolvable server id; keeping existing.')
            else:
                try:
                    notifications.put((0.5, _('Replacing existing…')))
                    log(f'Replacing existing copy (deleting book {remote.id}).')
                    backend.delete_book(remote.id)
                except PermissionDeniedError as e:
                    # 403 only: account-wide lack of delete permission.
                    batch.replace_disabled.set()
                    log(f'Cannot replace ({e}). Keeping existing, and falling '
                        f'back to "keep" for the rest of this send.')
                except BackendError as e:
                    log(f'Could not delete existing copy ({e}); keeping existing.')
                else:
                    # The old remote book is gone at this point. If upload fails,
                    # report that fact truthfully; there is no remote transaction.
                    remote_for_shelf = None
                    if abort.is_set():
                        log('Aborted after deleting the existing copy; replacement was not uploaded.')
                        return 'skipped'
                    notifications.put((0.6, _('Uploading…')))
                    try:
                        status = backend.upload(filepath, filename)
                    except Exception:
                        log('WARNING: existing copy was deleted, but replacement upload failed.')
                        raise
                    log(f'Sent OK ({status})')
                    result = 'replaced'

        else:
            if abort.is_set():
                log('Aborted.')
                return 'skipped'
            notifications.put((0.6, _('Uploading…')))
            status = backend.upload(filepath, filename)
            log(f'Sent OK ({status})')
            result = 'sent'

    except Exception as e:
        if is_connection_error(e):
            batch.server_down.set()
            log('Server unreachable (connection error) — stopping remaining sends in this send.')
        raise

    if shelf_name and backend is not None and backend.supports_shelves:
        notifications.put((0.85, _('Adding to shelf…')))
        try:
            remote = _resolved_book_for_shelf(backend, identity, remote_for_shelf, log)
            if remote is not None:
                shelf_id = backend.ensure_shelf(shelf_name)
                outcome = backend.add_to_shelf(shelf_id, remote.id)
                if outcome == 'already':
                    log(f'Already on shelf "{shelf_name}".')
                else:
                    log(f'Added to shelf "{shelf_name}".')
        except Exception as e:
            log(f'Warning: could not add to shelf "{shelf_name}": {e}')

    return result


def remove_one_book_job(backend_key, cfg, session_key, batch, identity, log,
                        abort, notifications):
    """Remove one safely-resolved remote book; never touches the local copy."""
    if batch.server_down.is_set():
        log('Server previously unreachable in this remove operation — skipping.')
        return 'skipped'
    if batch.delete_disabled.is_set():
        log('Remove disabled earlier in this operation (no delete permission) — skipping.')
        return 'skipped'

    try:
        notifications.put((0.1, _('Logging in…')))
        log(f'Book: {identity.title}')
        backend = get_connected_backend(backend_key, cfg, session_key, log=log)

        if abort.is_set():
            log('Aborted.')
            return 'skipped'

        notifications.put((0.45, _('Finding book on server…')))
        lookup = _lookup_or_skip(backend, identity, log)
        if lookup.status in (LookupStatus.UNKNOWN, LookupStatus.AMBIGUOUS):
            return 'skipped'
        if lookup.status == LookupStatus.NOT_FOUND:
            log('Book not found on server — nothing to remove.')
            return 'not_found'

        remote = lookup.book
        if remote is None or remote.id is None:
            log('Remote book has no resolvable server id — skipping for safety.')
            return 'skipped'

        if batch.delete_disabled.is_set():
            log('Remove disabled earlier in this operation (no delete permission) — skipping.')
            return 'skipped'

        if abort.is_set():
            log('Aborted.')
            return 'skipped'

        notifications.put((0.75, _('Removing from Calibre-Web…')))
        try:
            backend.delete_book(remote.id)
        except PermissionDeniedError as e:
            batch.delete_disabled.set()
            log(f'Cannot remove ({e}). Remaining removals in this operation will be skipped.')
            return 'skipped'

        log(f'Removed from Calibre-Web (book {remote.id}).')
        return 'removed'

    except Exception as e:
        if is_connection_error(e):
            batch.server_down.set()
            log('Server unreachable (connection error) — stopping remaining removals.')
        raise


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
        self.menu = self.qaction.menu()
        self.menu.aboutToShow.connect(self._build_menu)

    def _build_menu(self):
        from qt.core import QMenu
        self.menu.clear()
        P.migrate(prefs)
        profiles = P.get_profiles(prefs)
        _prune_shared_state_cache(profiles)
        active = prefs.get('active_profile')

        for p in profiles:
            name = p['name']
            act = self.menu.addAction(('✓ ' if name == active else '    ') + name)
            act.triggered.connect(lambda checked=False, n=name: self.send_to_profile(n))

        removable_profiles = []
        for p in profiles:
            backend_cls = get_backend_class(p.get('backend', DEFAULT_BACKEND))
            if p.get('allow_delete', False) and getattr(backend_cls, 'supports_delete', False):
                removable_profiles.append(p)

        if removable_profiles:
            self.menu.addSeparator()
            remove_sub = QMenu(_('Remove from Calibre-Web'), self.menu)
            for p in removable_profiles:
                name = p['name']
                a = remove_sub.addAction(('✓ ' if name == active else '    ') + name)
                a.triggered.connect(
                    lambda checked=False, n=name: self.remove_from_profile(n))
            self.menu.addMenu(remove_sub)

        self.menu.addSeparator()
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
        self.send_to_default()

    def remove_from_profile(self, name):
        profile = P.get_profile(prefs, name)
        if profile is None:
            return error_dialog(self.gui, _('Unknown profile'),
                                _('Profile "%s" no longer exists.') % name, show=True)
        self._remove_with_profile(profile)

    def _remove_with_profile(self, profile):
        backend_key = profile.get('backend', DEFAULT_BACKEND)
        backend_cls = get_backend_class(backend_key)
        if not profile.get('allow_delete', False):
            return info_dialog(
                self.gui, _('Remove disabled'),
                _('Removing books is disabled for profile "%s".') % profile['name'],
                show=True)
        if not getattr(backend_cls, 'supports_delete', False):
            return info_dialog(
                self.gui, _('Remove not supported'),
                _('The "%s" backend does not support removing books.') % backend_cls.name,
                show=True)

        rows = self.gui.library_view.selectionModel().selectedRows()
        if not rows:
            return error_dialog(self.gui, _('No books selected'),
                                _('Please select one or more books first.'), show=True)

        cfg = P.profile_to_config(profile)
        session_key = (
            profile.get('id') or profile['name'],
            profile.get('connection_revision', 0),
        )

        if not cfg['server_url']:
            return error_dialog(self.gui, _('Not configured'),
                                _('Profile "%s" has no server URL.') % profile['name'], show=True)

        db = self.gui.current_db.new_api
        identities = []
        for row in rows:
            book_id = self.gui.library_view.model().id(row)
            mi = db.get_metadata(book_id)
            identities.append(BookIdentity(
                mi.title,
                tuple(mi.authors or ['Unknown']),
                dict(getattr(mi, 'identifiers', {}) or {}),
            ))

        count = len(identities)
        if not question_dialog(
                self.gui, _('Remove from Calibre-Web?'),
                _('Remove %d selected book(s) from "%s"?') % (count, profile['name']) +
                '\n\n' +
                _('Only unambiguous matches on the remote server will be removed.\n'
                  'Your local Calibre books will not be changed.'),
                show_copy_button=False):
            return

        try:
            get_connected_backend(
                backend_key, cfg, session_key, log=None, validate=True)
        except AuthenticationError as e:
            return error_dialog(self.gui, _('Login failed'),
                                _('Could not log in to "%s":') % profile['name'] + f'\n\n{e}', show=True)
        except BackendError as e:
            return error_dialog(self.gui, _('Cannot remove books'),
                                _('Could not prepare "%s" for removal:') % profile['name'] + f'\n\n{e}', show=True)
        except Exception as e:
            return error_dialog(self.gui, _('Cannot reach server'),
                                _('Could not connect to "%s":') % profile['name'] + f'\n\n{e}', show=True)

        batch = SendBatchContext()
        for identity in identities:
            job = ThreadedJob(
                'remove_from_calibre_web',
                _('Removing "%s" from %s') % (identity.title, profile['name']),
                remove_one_book_job,
                (backend_key, cfg, session_key, batch, identity),
                {},
                self.send_complete,
            )
            self.gui.job_manager.run_threaded_job(job)

        self.gui.status_bar.show_message(
            _('Queued %d book(s) for removal from %s') % (count, profile['name']), 3000)

    def _send_with_profile(self, profile):
        rows = self.gui.library_view.selectionModel().selectedRows()
        if not rows:
            return error_dialog(self.gui, _('No books selected'),
                                _('Please select one or more books first.'), show=True)

        cfg = P.profile_to_config(profile)
        backend_key = profile.get('backend', DEFAULT_BACKEND)
        format_order = profile.get('format_order', 'epub,mobi,azw3,fb2,pdf')
        session_key = (
            profile.get('id') or profile['name'],
            profile.get('connection_revision', 0),
        )

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
            authors = tuple(mi.authors or ['Unknown'])
            identifiers = dict(getattr(mi, 'identifiers', {}) or {})
            identity = BookIdentity(mi.title, authors, identifiers)
            book_data.append((book_id, identity, filepath, filename))

        if missing_format:
            names = '\n'.join(missing_format)
            if not question_dialog(
                    self.gui, _('Missing formats'),
                    _('The following books have no sendable format and will be skipped:') +
                    f'\n{names}\n\n' + _('Continue?')):
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
        batch = SendBatchContext()

        # Pre-flight establishes shared session state once, without sharing the
        # backend wrapper/logger across jobs.
        try:
            backend = get_connected_backend(backend_key, cfg, session_key, log=None, validate=True)
        except AuthenticationError as e:
            return error_dialog(self.gui, _('Login failed'),
                                _('Could not log in to "%s":') % profile['name'] + f'\n\n{e}', show=True)
        except BackendError as e:
            return error_dialog(self.gui, _('Login failed'),
                                _('Could not log in to "%s":') % profile['name'] + f'\n\n{e}', show=True)
        except Exception as e:
            return error_dialog(self.gui, _('Cannot reach server'),
                                _('Could not connect to "%s":') % profile['name'] + f'\n\n{e}', show=True)

        if duplicate_policy in ('replace', 'ask') and not getattr(backend, 'supports_replace', False):
            duplicate_policy = 'keep'

        if duplicate_policy == 'ask':
            answer = question_dialog(
                self.gui, _('Replace existing books?'),
                _('If any of the selected books already exist on "%s", replace them?') %
                profile['name'] + '\n\n' +
                _('Yes — replace duplicates (delete the existing copy then upload).\n'
                  'No — keep the existing copies and skip those.'),
                show_copy_button=False)
            duplicate_policy = 'replace' if answer else 'keep'

        for book_id, identity, filepath, filename in book_data:
            job = ThreadedJob(
                'send_to_calibre_web',
                _('Sending "%s" to %s') % (identity.title, profile['name']),
                send_one_book_job,
                (backend_key, cfg, session_key, batch, identity, filepath, filename,
                 shelf_name, duplicate_policy),
                {},
                self.send_complete,
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
