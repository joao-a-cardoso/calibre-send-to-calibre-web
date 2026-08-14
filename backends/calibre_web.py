# -*- coding: utf-8 -*-
# Copyright (C) 2026 João Cardoso
# License: GNU General Public License v3 (see LICENSE)

"""Calibre-web backend driver.

Encapsulates Calibre-web's session+CSRF login, OPDS lookup, multipart upload,
delete, and shelf endpoints.  Authenticated browser-session state is shared
between lightweight per-job backend instances; job-specific loggers are not.
"""

import base64
import http.cookiejar
import re
import ssl
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from calibre_plugins.send_to_calibre_web.backends.base import (
    AuthenticationError, Backend, BackendError, LookupResult,
    PermissionDeniedError, ProtocolError, RemoteBook, SessionExpiredError,
)

CSRF_RE = re.compile(rb'name="csrf_token"[^>]*value="([^"]+)"')
DOWNLOAD_LINK_RE = re.compile(r'/opds/download/(\d+)/')
SHELF_LINK_RE = re.compile(r'/opds/shelf/(\d+)')


def _basic_auth_header(username, password):
    creds = base64.b64encode(f'{username}:{password}'.encode()).decode()
    return f'Basic {creds}'


def _extract_error_text(resp_body):
    try:
        text = resp_body.decode('utf-8', 'replace')
    except Exception:
        return ''
    m = re.search(r'class="[^"]*alert[^"]*"[^>]*>(.*?)<', text, re.S | re.I)
    if m:
        hint = re.sub(r'\s+', ' ', m.group(1)).strip()
        if hint:
            return f': {hint[:200]}'
    return ''


def _norm_text(value):
    value = unicodedata.normalize('NFKC', value or '')
    return ' '.join(value.casefold().split())


def _norm_identifier(value):
    return re.sub(r'[^0-9a-z]', '', (value or '').casefold())


def _normalise_identifiers(identifiers):
    out = {}
    for key, value in (identifiers or {}).items():
        nk = _norm_text(str(key))
        nv = _norm_identifier(str(value))
        if nk and nv:
            out[nk] = nv
    return out


def _entry_identifiers(entry):
    """Extract identifiers exposed by OPDS, with special handling for ISBN."""
    out = {}
    for elem in entry.iter():
        if elem.tag.rsplit('}', 1)[-1].lower() != 'identifier' or not elem.text:
            continue
        raw = elem.text.strip()
        low = raw.casefold()
        compact = _norm_identifier(raw)
        if 'isbn' in low:
            digits = re.sub(r'[^0-9xX]', '', raw)
            if len(digits) in (10, 13):
                out['isbn'] = digits.casefold()
        scheme = elem.get('scheme') or elem.get('{http://www.w3.org/2001/XMLSchema-instance}type')
        if scheme and compact:
            out[_norm_text(scheme)] = compact
    return out


class _CalibreWebSession:
    """Thread-safe authenticated browser session shared by jobs for one profile."""

    def __init__(self, server_url, username, password, verify_ssl):
        self.server_url = server_url.rstrip('/')
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self._lock = threading.RLock()
        self._opener = None

    def _new_opener(self):
        jar = http.cookiejar.CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(jar)]
        if not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        return urllib.request.build_opener(*handlers)

    def _raw_csrf(self, opener, url):
        req = urllib.request.Request(url)
        with opener.open(req, timeout=15) as resp:
            body = resp.read()
        m = CSRF_RE.search(body)
        return m.group(1).decode() if m else None

    def _login_locked(self):
        self._opener = self._new_opener()
        try:
            token = self._raw_csrf(self._opener, f'{self.server_url}/login')
            fields = {
                'username': self.username,
                'password': self.password,
                'submit': '',
                'next': '/',
            }
            if token:
                fields['csrf_token'] = token
            data = urllib.parse.urlencode(fields).encode()
            req = urllib.request.Request(f'{self.server_url}/login', data=data)
            req.add_header('Content-Type', 'application/x-www-form-urlencoded')
            with self._opener.open(req, timeout=15) as resp:
                final_url = resp.geturl()
                body = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthenticationError('Login failed: check username/password.')
            raise BackendError(f'login error (HTTP {e.code})')
        if '/login' in final_url and CSRF_RE.search(body):
            raise AuthenticationError('Login failed: check username/password.')

    def ensure_connected(self, validate=False):
        """Ensure a session exists; optionally validate it during GUI pre-flight."""
        with self._lock:
            if self._opener is None:
                self._login_locked()
                return True
            if not validate:
                return False
            try:
                req = urllib.request.Request(f'{self.server_url}/')
                with self._opener.open(req, timeout=10) as resp:
                    if '/login' not in resp.geturl():
                        resp.read()
                        return False
            except urllib.error.HTTPError as e:
                if e.code != 401:
                    raise
            # Expired/invalid session: establish a new one while still holding
            # the re-entrant lock. Connection errors propagate to pre-flight.
            self._login_locked()
            return True

    def run(self, operation):
        """Run one authenticated operation, re-login and retry once on expiry.

        The lock intentionally covers the full browser-session operation because
        urllib's opener + CookieJar are mutable shared state.  OPDS reads do not
        use this path and may still run concurrently.
        """
        with self._lock:
            if self._opener is None:
                self._login_locked()
            for attempt in range(2):
                try:
                    return operation(self._opener)
                except SessionExpiredError:
                    if attempt:
                        raise AuthenticationError(
                            'Calibre-web session expired and re-login did not recover it.')
                    self._login_locked()
        raise AuthenticationError('Could not establish a Calibre-web session.')


class CalibreWebBackend(Backend):

    key = 'calibre-web'
    name = 'Calibre-web'
    supports_duplicate_check = True
    supports_shelves = True
    supports_delete = True
    supports_replace = True

    def __init__(self, config, log=None, shared_state=None):
        Backend.__init__(self, config, log=log, shared_state=shared_state)
        self.server_url = config['server_url'].rstrip('/')
        self.username = config.get('username', '')
        self.password = config.get('password', '')
        self.verify_ssl = config.get('verify_ssl', True)
        if shared_state is None:
            shared_state = _CalibreWebSession(
                self.server_url, self.username, self.password, self.verify_ssl)
            self._shared_state = shared_state
        self._session = shared_state

    # --- helpers -----------------------------------------------------------
    def _new_stateless_opener(self):
        handlers = []
        if not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        return urllib.request.build_opener(*handlers)

    def _opds_get(self, path, timeout=15):
        url = path if path.startswith('http') else f'{self.server_url}{path}'
        req = urllib.request.Request(url)
        if self.username:
            req.add_header('Authorization', _basic_auth_header(self.username, self.password))
        opener = self._new_stateless_opener()
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise AuthenticationError(
                    'OPDS authentication failed (HTTP 401) — check username/password.')
            if e.code == 403:
                raise PermissionDeniedError('OPDS access forbidden (HTTP 403).')
            raise BackendError(f'OPDS request failed (HTTP {e.code})')

    @staticmethod
    def _check_session_response(resp):
        if '/login' in resp.geturl():
            raise SessionExpiredError('server redirected to login page')

    def _csrf_once(self, opener, url):
        req = urllib.request.Request(url)
        try:
            with opener.open(req, timeout=15) as resp:
                self._check_session_response(resp)
                body = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise SessionExpiredError('session expired (HTTP 401)')
            raise
        m = CSRF_RE.search(body)
        return m.group(1).decode() if m else None

    # --- lifecycle ---------------------------------------------------------
    def connect(self, validate=False):
        fresh = self._session.ensure_connected(validate=validate)
        self.log('Logged in to Calibre-web.' if fresh
                 else 'Reusing existing Calibre-web session.')

    def test_connection(self):
        try:
            data = self._opds_get('/opds', timeout=10)
            if data is not None:
                return True, 'Connection OK — OPDS catalog reachable.'
            return False, 'Unexpected empty response from OPDS catalog.'
        except urllib.error.HTTPError as e:
            if e.code == 429:
                return False, 'Server is rate-limiting requests (HTTP 429) — wait a minute and retry.'
            return False, f'Server error: HTTP {e.code}'
        except Exception as e:
            return False, f'Connection failed: {e}'

    # --- remote-book lookup -----------------------------------------------
    def find_book(self, identity):
        query = urllib.parse.quote(identity.title)
        try:
            data = self._opds_get(f'/opds/search?query={query}', timeout=15)
            root = ET.fromstring(data)
        except (AuthenticationError, PermissionDeniedError):
            raise
        except urllib.error.URLError:
            raise
        except BackendError as e:
            self.log(f'Warning: duplicate lookup failed ({e}).')
            return LookupResult.unknown(str(e))
        except ET.ParseError as e:
            detail = f'invalid OPDS response: {e}'
            self.log(f'Warning: duplicate lookup failed ({detail}).')
            return LookupResult.unknown(detail)

        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        wanted_title = _norm_text(identity.title)
        candidates = []
        for entry in root.findall('atom:entry', ns):
            title_elem = entry.find('atom:title', ns)
            if title_elem is None or not title_elem.text:
                continue
            remote_title = title_elem.text.strip()
            if _norm_text(remote_title) != wanted_title:
                continue

            authors = []
            for author_elem in entry.findall('atom:author', ns):
                name = author_elem.find('atom:name', ns)
                if name is not None and name.text:
                    authors.append(name.text.strip())

            book_id = None
            for link in entry.findall('atom:link', ns):
                m = DOWNLOAD_LINK_RE.search(link.get('href', ''))
                if m:
                    book_id = int(m.group(1))
                    break

            candidates.append(RemoteBook(
                id=book_id,
                title=remote_title,
                authors=tuple(authors),
                identifiers=_entry_identifiers(entry),
            ))

        if not candidates:
            return LookupResult.not_found()

        local_ids = _normalise_identifiers(identity.identifiers)
        if local_ids:
            id_matches = []
            for candidate in candidates:
                remote_ids = _normalise_identifiers(candidate.identifiers)
                if any(remote_ids.get(k) == v for k, v in local_ids.items() if k in remote_ids):
                    id_matches.append(candidate)
            if len(id_matches) == 1:
                return LookupResult.found(id_matches[0])
            if len(id_matches) > 1:
                return LookupResult.ambiguous('multiple books match the same identifier')

        local_authors = {_norm_text(a) for a in identity.authors if _norm_text(a)}
        if local_authors:
            author_matches = []
            for candidate in candidates:
                remote_authors = {_norm_text(a) for a in candidate.authors if _norm_text(a)}
                if remote_authors and local_authors.intersection(remote_authors):
                    author_matches.append(candidate)
            if len(author_matches) == 1:
                return LookupResult.found(author_matches[0])
            if len(author_matches) > 1:
                return LookupResult.ambiguous('multiple books match title and author')

        if len(candidates) == 1:
            candidate = candidates[0]
            remote_authors = {_norm_text(a) for a in candidate.authors if _norm_text(a)}
            if local_authors and remote_authors and not local_authors.intersection(remote_authors):
                return LookupResult.not_found('same title exists, but author differs')
            return LookupResult.found(candidate)

        return LookupResult.ambiguous(
            f'{len(candidates)} books share this title and cannot be distinguished safely')

    # --- upload ------------------------------------------------------------
    def upload(self, filepath, filename):
        def operation(opener):
            url = f'{self.server_url}/upload'
            token = self._csrf_once(opener, f'{self.server_url}/')
            safe = filename.replace('\r', '').replace('\n', '').replace('"', '\\"')
            boundary = '----CalibreWebPluginBoundary'
            with open(filepath, 'rb') as f:
                file_data = f.read()
            parts = []
            if token:
                parts.append((f'--{boundary}\r\n'
                              f'Content-Disposition: form-data; name="csrf_token"\r\n\r\n'
                              f'{token}\r\n').encode())
            parts.append((f'--{boundary}\r\n'
                          f'Content-Disposition: form-data; name="btn-upload"; filename="{safe}"\r\n'
                          f'Content-Type: application/octet-stream\r\n\r\n').encode())
            parts.append(file_data)
            parts.append(f'\r\n--{boundary}--\r\n'.encode())
            body = b''.join(parts)

            req = urllib.request.Request(url, data=body)
            req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
            req.add_header('Content-Length', str(len(body)))
            try:
                with opener.open(req, timeout=120) as resp:
                    self._check_session_response(resp)
                    status = resp.status
                    resp_body = resp.read()
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise SessionExpiredError('session expired during upload')
                body_bytes = b''
                try:
                    body_bytes = e.read()
                except Exception:
                    pass
                snippet = _extract_error_text(body_bytes)
                if e.code == 403:
                    raise PermissionDeniedError(f'not allowed to upload (HTTP 403){snippet}')
                if e.code == 422:
                    raise BackendError(
                        f'Calibre-web rejected the file (HTTP 422){snippet}. '
                        f'Check that this format is allowed and uploads are enabled.')
                raise BackendError(f'server error (HTTP {e.code}){snippet}')

            if status not in (200, 201):
                snippet = _extract_error_text(resp_body)
                raise BackendError(f'server rejected upload (HTTP {status}){snippet}')
            return f'HTTP {status}'

        return self._session.run(operation)

    def delete_book(self, book_id):
        def operation(opener):
            token = self._csrf_once(opener, f'{self.server_url}/')
            fields = {}
            if token:
                fields['csrf_token'] = token
            data = urllib.parse.urlencode(fields).encode()
            req = urllib.request.Request(f'{self.server_url}/delete/{book_id}', data=data)
            req.add_header('Content-Type', 'application/x-www-form-urlencoded')
            req.add_header('X-Requested-With', 'XMLHttpRequest')
            if token:
                req.add_header('X-CSRFToken', token)
            try:
                with opener.open(req, timeout=30) as resp:
                    self._check_session_response(resp)
                    status = resp.status
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise SessionExpiredError('session expired during delete')
                if e.code == 403:
                    raise PermissionDeniedError(
                        'not allowed to delete (the Calibre-web user needs the '
                        '"Delete books" permission)')
                raise BackendError(f'delete failed (HTTP {e.code})')
            if status not in (200, 204):
                raise BackendError(f'delete returned HTTP {status}')

        return self._session.run(operation)

    # --- shelves -----------------------------------------------------------
    def _list_shelves(self):
        data = self._opds_get('/opds/shelfindex', timeout=15)
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            raise ProtocolError(f'invalid shelf-index OPDS response: {e}')
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        shelves = {}
        for entry in root.findall('atom:entry', ns):
            t = entry.find('atom:title', ns)
            eid = entry.find('atom:id', ns)
            candidates = [eid.text if eid is not None else '']
            for link in entry.findall('atom:link', ns):
                candidates.append(link.get('href', ''))
            for candidate in candidates:
                m = SHELF_LINK_RE.search(candidate or '')
                if m and t is not None and t.text:
                    shelves[_norm_text(t.text)] = int(m.group(1))
                    break
        return shelves

    def ensure_shelf(self, shelf_name):
        shelves = self._list_shelves()
        sid = shelves.get(_norm_text(shelf_name))
        if sid is not None:
            return sid

        def operation(opener):
            token = self._csrf_once(opener, f'{self.server_url}/shelf/create')
            fields = {'title': shelf_name}
            if token:
                fields['csrf_token'] = token
            data = urllib.parse.urlencode(fields).encode()
            req = urllib.request.Request(f'{self.server_url}/shelf/create', data=data)
            req.add_header('Content-Type', 'application/x-www-form-urlencoded')
            try:
                with opener.open(req, timeout=15) as resp:
                    self._check_session_response(resp)
                    resp.read()
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise SessionExpiredError('session expired while creating shelf')
                if e.code == 403:
                    raise PermissionDeniedError('not allowed to create shelves')
                raise BackendError(f'shelf creation failed (HTTP {e.code})')

        self._session.run(operation)
        self.log(f'Created shelf "{shelf_name}".')
        shelves = self._list_shelves()
        sid = shelves.get(_norm_text(shelf_name))
        if sid is None:
            raise BackendError(f'shelf "{shelf_name}" not found after creation')
        return sid

    def add_to_shelf(self, shelf_id, book_id):
        def operation(opener):
            token = self._csrf_once(opener, f'{self.server_url}/')
            req = urllib.request.Request(
                f'{self.server_url}/shelf/add/{shelf_id}/{book_id}', data=b'')
            req.add_header('X-Requested-With', 'XMLHttpRequest')
            if token:
                req.add_header('X-CSRFToken', token)
            try:
                with opener.open(req, timeout=15) as resp:
                    self._check_session_response(resp)
                    return 'added'
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise SessionExpiredError('session expired while adding to shelf')
                body = ''
                try:
                    body = e.read().decode('utf-8', 'replace')
                except Exception:
                    pass
                if e.code == 400 and 'already part of the shelf' in body.casefold():
                    return 'already'
                if e.code == 403:
                    raise PermissionDeniedError(
                        'not allowed to add to this shelf (check user shelf permissions)')
                raise BackendError(f'shelf add failed (HTTP {e.code}): {body[:200]}')

        return self._session.run(operation)
