# -*- coding: utf-8 -*-
# Copyright (C) 2026 Claude (Anthropic) and João Cardoso
# License: GNU General Public License v3 (see LICENSE)

import os
import threading
import re
import ssl
import http.cookiejar
import urllib.request
import urllib.parse
import base64
import xml.etree.ElementTree as ET

from calibre.gui2.actions import InterfaceAction
from calibre.gui2 import error_dialog, info_dialog, question_dialog
try:
    from calibre.gui2.threaded_jobs import ThreadedJob
except ImportError:
    from calibre.gui2.jobs import ThreadedJob

from calibre_plugins.send_to_calibre_web.config import prefs

load_translations()

CSRF_RE = re.compile(rb'name="csrf_token"[^>]*value="([^"]+)"')

# Session reuse across jobs: cache one logged-in opener per
# (server, user, password, verify_ssl) combination. Guarded by a lock
# since ThreadedJobs may overlap in edge cases.
_session_lock = threading.Lock()
_session_cache = {}


def session_valid(opener, server_url):
    """Cheap check that a cached session is still logged in."""
    try:
        req = urllib.request.Request(f'{server_url}/')
        with opener.open(req, timeout=10) as resp:
            return '/login' not in resp.geturl()
    except Exception:
        return False


def get_session(server_url, username, password, verify_ssl, log=None):
    """Return a logged-in opener, reusing a cached session when valid.

    Raises if login fails.
    """
    key = (server_url, username, password, verify_ssl)
    with _session_lock:
        opener = _session_cache.get(key)
    if opener is not None:
        if session_valid(opener, server_url):
            if log:
                log('Reusing existing Calibre-web session.')
            return opener
        with _session_lock:
            _session_cache.pop(key, None)
        if log:
            log('Cached session expired, logging in again.')
    opener = get_opener(verify_ssl)
    if not cw_login(opener, server_url, username, password):
        raise Exception(_('Login failed: check username/password.'))
    if log:
        log('Logged in to Calibre-web.')
    with _session_lock:
        _session_cache[key] = opener
    return opener


def get_opener(verify_ssl):
    """Return a urllib opener with a cookie jar, optionally ignoring SSL errors."""
    jar = http.cookiejar.CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(jar)]
    if not verify_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def get_csrf_token(opener, url):
    """Fetch a page and extract the Flask-WTF csrf_token, or None."""
    req = urllib.request.Request(url)
    with opener.open(req, timeout=15) as resp:
        body = resp.read()
    m = CSRF_RE.search(body)
    return m.group(1).decode() if m else None


def cw_login(opener, server_url, username, password):
    """Log into Calibre-web, establishing a session cookie.

    Returns True on success. Raises on network errors.
    """
    token = get_csrf_token(opener, f'{server_url}/login')
    fields = {
        'username': username,
        'password': password,
        'submit': '',
        'next': '/',
    }
    if token:
        fields['csrf_token'] = token
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(f'{server_url}/login', data=data)
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    with opener.open(req, timeout=15) as resp:
        final_url = resp.geturl()
        body = resp.read()
    # On success Calibre-web redirects away from /login.
    if '/login' in final_url and CSRF_RE.search(body):
        return False
    return True


def basic_auth_header(username, password):
    creds = base64.b64encode(f'{username}:{password}'.encode()).decode()
    return f'Basic {creds}'


def opds_book_exists(opener, server_url, username, password, title, authors, log=None):
    """Check OPDS catalog for a book with matching title. Returns True if found.

    Calibre-web's search endpoint takes a 'query' parameter
    (cps/opds.py: request.args.get("query")).

    Fails open: any network/parse error returns False, so the book is
    uploaded rather than silently skipped.
    """
    try:
        query = urllib.parse.quote(title)
        url = f'{server_url}/opds/search?query={query}'
        req = urllib.request.Request(url)
        if username:
            req.add_header('Authorization', basic_auth_header(username, password))
        with opener.open(req, timeout=15) as resp:
            data = resp.read()
        root = ET.fromstring(data)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        entries = root.findall('atom:entry', ns)
        title_lower = title.lower().strip()
        for entry in entries:
            t = entry.find('atom:title', ns)
            if t is not None and t.text and t.text.lower().strip() == title_lower:
                return True
        return False
    except Exception as e:
        if log:
            log(f'Warning: duplicate check failed ({e}), uploading anyway.')
        return False


def upload_book(opener, server_url, username, password, filepath, filename):
    """Upload a book file to Calibre-web via multipart POST using the
    established session. Returns the HTTP status on success; raises
    RuntimeError if the server bounced us to the login page (auth issue)
    or rejected the upload.
    """
    url = f'{server_url}/upload'

    # CSRF token from the main page (any session page carries the form token)
    token = get_csrf_token(opener, f'{server_url}/')

    # Sanitize filename for the Content-Disposition header: strip CR/LF
    # and escape double quotes, which would otherwise corrupt the
    # multipart body (RFC 7578).
    safe_filename = filename.replace('\r', '').replace('\n', '').replace('"', '\\"')

    boundary = '----CalibreWebPluginBoundary'
    with open(filepath, 'rb') as f:
        file_data = f.read()

    parts = []
    if token:
        parts.append(
            (f'--{boundary}\r\n'
             f'Content-Disposition: form-data; name="csrf_token"\r\n\r\n'
             f'{token}\r\n').encode())
    parts.append(
        (f'--{boundary}\r\n'
         f'Content-Disposition: form-data; name="btn-upload"; filename="{safe_filename}"\r\n'
         f'Content-Type: application/octet-stream\r\n\r\n').encode())
    parts.append(file_data)
    parts.append(f'\r\n--{boundary}--\r\n'.encode())
    body = b''.join(parts)

    req = urllib.request.Request(url, data=body)
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
    req.add_header('Content-Length', str(len(body)))

    with opener.open(req, timeout=120) as resp:
        final_url = resp.geturl()
        status = resp.status
        resp_body = resp.read()

    # If we were redirected to the login page, the session is invalid:
    # report a real error instead of a false success.
    if '/login' in final_url:
        raise RuntimeError('not logged in - server redirected to login page')
    if status not in (200, 201):
        raise RuntimeError(f'unexpected HTTP status {status}')
    return status


def select_format(db, book_id, format_order):
    """Select the best available format for this book according to preference order."""
    available = db.formats(book_id)
    if not available:
        return None, None
    available_upper = [f.upper() for f in available]
    for fmt in [f.strip().upper() for f in format_order.split(',')]:
        if fmt in available_upper:
            idx = available_upper.index(fmt)
            return available[idx], fmt
    # fallback: first available
    return available[0], available[0].upper()


DOWNLOAD_LINK_RE = re.compile(r'/opds/download/(\d+)/')
SHELF_LINK_RE = re.compile(r'/opds/shelf/(\d+)')


def opds_find_book_id(opener, server_url, username, password, title, log=None):
    """Search OPDS for an exact title match and return its calibre-web
    book id (parsed from the acquisition/download link), or None."""
    try:
        query = urllib.parse.quote(title)
        url = f'{server_url}/opds/search?query={query}'
        req = urllib.request.Request(url)
        if username:
            req.add_header('Authorization', basic_auth_header(username, password))
        with opener.open(req, timeout=15) as resp:
            data = resp.read()
        root = ET.fromstring(data)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        title_lower = title.lower().strip()
        for entry in root.findall('atom:entry', ns):
            t = entry.find('atom:title', ns)
            if t is None or not t.text or t.text.lower().strip() != title_lower:
                continue
            for link in entry.findall('atom:link', ns):
                m = DOWNLOAD_LINK_RE.search(link.get('href', ''))
                if m:
                    return int(m.group(1))
        return None
    except Exception as e:
        if log:
            log(f'Warning: could not find book id ({e}).')
        return None


def opds_list_shelves(opener, server_url, username, password):
    """Return {shelf_name_lower: shelf_id} from /opds/shelfindex."""
    url = f'{server_url}/opds/shelfindex'
    req = urllib.request.Request(url)
    if username:
        req.add_header('Authorization', basic_auth_header(username, password))
    with opener.open(req, timeout=15) as resp:
        data = resp.read()
    root = ET.fromstring(data)
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    shelves = {}
    for entry in root.findall('atom:entry', ns):
        t = entry.find('atom:title', ns)
        eid = entry.find('atom:id', ns)
        candidates = [eid.text if eid is not None else '']
        for link in entry.findall('atom:link', ns):
            candidates.append(link.get('href', ''))
        for c in candidates:
            m = SHELF_LINK_RE.search(c or '')
            if m and t is not None and t.text:
                shelves[t.text.strip().lower()] = int(m.group(1))
                break
    return shelves


def ensure_shelf(opener, server_url, username, password, shelf_name, log=None):
    """Return the shelf id for shelf_name, creating the shelf if needed."""
    shelves = opds_list_shelves(opener, server_url, username, password)
    sid = shelves.get(shelf_name.strip().lower())
    if sid is not None:
        return sid
    # Create it (session + CSRF, like the web UI does)
    token = get_csrf_token(opener, f'{server_url}/shelf/create')
    fields = {'title': shelf_name}
    if token:
        fields['csrf_token'] = token
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(f'{server_url}/shelf/create', data=data)
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    with opener.open(req, timeout=15) as resp:
        resp.read()
    if log:
        log(f'Created shelf "{shelf_name}".')
    shelves = opds_list_shelves(opener, server_url, username, password)
    sid = shelves.get(shelf_name.strip().lower())
    if sid is None:
        raise RuntimeError(f'shelf "{shelf_name}" not found after creation')
    return sid


def add_book_to_shelf(opener, server_url, shelf_id, book_id):
    """POST /shelf/add/<shelf_id>/<book_id> using the session."""
    token = get_csrf_token(opener, f'{server_url}/')
    fields = {}
    if token:
        fields['csrf_token'] = token
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(f'{server_url}/shelf/add/{shelf_id}/{book_id}', data=data)
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    req.add_header('X-Requested-With', 'XMLHttpRequest')
    if token:
        req.add_header('X-CSRFToken', token)
    with opener.open(req, timeout=15) as resp:
        return resp.status


def send_one_book_job(server_url, username, password, verify_ssl,
                      title, authors, filepath, filename, shelf_name,
                      log, abort, notifications):
    """Send a single book; one ThreadedJob per book.

    Returns 'sent' or 'skipped'. Raises on login/upload failure so the
    job is marked as failed in the jobs panel. If shelf_name is set,
    the book is added to that shelf (created if missing) — including
    books skipped as duplicates, so re-sending assigns shelves.
    """
    notifications.put((0.1, _('Logging in…')))
    log(f'Book: {title}')
    opener = get_session(server_url, username, password, verify_ssl, log=log)

    if abort.is_set():
        log('Aborted.')
        return 'skipped'

    notifications.put((0.4, _('Checking for duplicate…')))
    result = 'skipped'
    if opds_book_exists(opener, server_url, username, password, title, authors, log=log):
        log('Already exists on server, skipping upload.')
    else:
        if abort.is_set():
            log('Aborted.')
            return 'skipped'
        notifications.put((0.6, _('Uploading…')))
        status = upload_book(opener, server_url, username, password, filepath, filename)
        log(f'Sent OK (HTTP {status})')
        result = 'sent'

    if shelf_name:
        notifications.put((0.85, _('Adding to shelf…')))
        book_id = opds_find_book_id(opener, server_url, username, password, title, log=log)
        if book_id is None:
            log(f'Warning: book not found on server, cannot add to shelf "{shelf_name}".')
        else:
            shelf_id = ensure_shelf(opener, server_url, username, password, shelf_name, log=log)
            add_book_to_shelf(opener, server_url, shelf_id, book_id)
            log(f'Added to shelf "{shelf_name}".')

    return result


def send_books_job(server_url, username, password, verify_ssl, format_order,
                   book_data, log, abort, notifications):
    """The actual work done in the background thread."""
    opener = get_opener(verify_ssl)
    total = len(book_data)
    sent = 0
    skipped = 0
    errors = []

    # Establish a Calibre-web session (the /upload endpoint requires a
    # logged-in session; Basic auth only works for OPDS).
    try:
        if not cw_login(opener, server_url, username, password):
            log('Login failed: check username/password.')
            log('\nDone. Sent: 0, Skipped (duplicates): 0, Errors: %d' % total)
            return
        log('Logged in to Calibre-web.')
    except Exception as e:
        log(f'Login error: {e}')
        log('\nDone. Sent: 0, Skipped (duplicates): 0, Errors: %d' % total)
        return

    for i, (book_id, title, authors, filepath, filename) in enumerate(book_data):
        if abort.is_set():
            log('Aborted.')
            break
        notifications.put((i / total, f'Sending {title}…'))
        log(f'Checking: {title}')

        if opds_book_exists(opener, server_url, username, password, title, authors, log=log):
            log(f'  → Already exists, skipping.')
            skipped += 1
            continue

        try:
            status = upload_book(opener, server_url, username, password, filepath, filename)
            log(f'  → Sent OK (HTTP {status})')
            sent += 1
        except Exception as e:
            msg = f'  → Error: {e}'
            log(msg)
            errors.append(f'{title}: {e}')

    log(f'\nDone. Sent: {sent}, Skipped (duplicates): {skipped}, Errors: {len(errors)}')
    if errors:
        log('\nErrors:')
        for e in errors:
            log(f'  {e}')


class SendToCalibreWebAction(InterfaceAction):

    name = 'Send to Calibre-web'
    action_spec = (_('Send to Calibre-web'), None,
                   _('Send selected books to Calibre-web server'), None)
    action_type = 'current'
    dont_add_to = frozenset(['context-menu-device'])

    def genesis(self):
        icon = get_icons('images/icon.png', 'Send to Calibre-web')
        self.qaction.setIcon(icon)
        self.qaction.triggered.connect(self.send_books)

    def send_books(self):
        rows = self.gui.library_view.selectionModel().selectedRows()
        if not rows:
            return error_dialog(self.gui, _('No books selected'),
                                _('Please select one or more books first.'), show=True)

        server_url   = prefs['server_url']
        username     = prefs['username']
        password     = prefs['password']
        verify_ssl   = prefs['verify_ssl']
        format_order = prefs['format_order']

        if not server_url:
            return error_dialog(self.gui, _('Not configured'),
                                _('Please configure the Calibre-web server URL in plugin preferences.'), show=True)

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
            return info_dialog(self.gui, _('Nothing to send'), _('No books with a suitable format were found.'), show=True)

        shelf_name = ''
        if prefs['add_to_shelf']:
            shelf_name = prefs['shelf_name'].strip()
            if not shelf_name:
                # Default: name of the currently open Calibre library
                shelf_name = os.path.basename(
                    os.path.normpath(self.gui.current_db.library_path))

        for book_id, title, authors, filepath, filename in book_data:
            job = ThreadedJob(
                'send_to_calibre_web',
                _('Sending "%s" to Calibre-web') % title,
                send_one_book_job,
                (server_url, username, password, verify_ssl,
                 title, authors, filepath, filename, shelf_name),
                {},
                self.send_complete
            )
            self.gui.job_manager.run_threaded_job(job)
        self.gui.status_bar.show_message(
            _('Queued %d book(s) for Calibre-web') % len(book_data), 3000)

    def send_complete(self, job):
        if job.failed:
            return error_dialog(self.gui, _('Send failed'),
                                _('An error occurred while sending: %s') % job.description,
                                det_msg=job.details, show=True)
        self.gui.status_bar.show_message(job.description + ' — ' + _('done'), 3000)
