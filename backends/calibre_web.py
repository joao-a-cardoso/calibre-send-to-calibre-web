# -*- coding: utf-8 -*-
# Copyright (C) 2026 João Cardoso
# License: GNU General Public License v3 (see LICENSE)

"""Calibre-web backend driver.

Encapsulates Calibre-web's session+CSRF login, OPDS duplicate detection,
multipart upload, and shelf endpoints. Behavior is preserved verbatim from the
original single-backend implementation; only the structure changed.
"""

import re
import ssl
import base64
import http.cookiejar
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET

from calibre_plugins.send_to_calibre_web.backends.base import Backend, BackendError

CSRF_RE = re.compile(rb'name="csrf_token"[^>]*value="([^"]+)"')
DOWNLOAD_LINK_RE = re.compile(r'/opds/download/(\d+)/')
SHELF_LINK_RE = re.compile(r'/opds/shelf/(\d+)')


def _basic_auth_header(username, password):
    creds = base64.b64encode(f'{username}:{password}'.encode()).decode()
    return f'Basic {creds}'


def _extract_error_text(resp_body):
    """Pull a short human-readable error hint from a Calibre-web HTML
    response, if present. Returns '' or ': <hint>'."""
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


class CalibreWebBackend(Backend):

    key = 'calibre-web'
    name = 'Calibre-web'
    supports_duplicate_check = True
    supports_shelves = True

    def __init__(self, config, log=None):
        Backend.__init__(self, config, log=log)
        self.server_url = config['server_url'].rstrip('/')
        self.username = config.get('username', '')
        self.password = config.get('password', '')
        self.verify_ssl = config.get('verify_ssl', True)
        self._opener = None

    # --- opener / session ---
    def _new_opener(self):
        jar = http.cookiejar.CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(jar)]
        if not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        return urllib.request.build_opener(*handlers)

    def _csrf(self, url):
        req = urllib.request.Request(url)
        with self._opener.open(req, timeout=15) as resp:
            body = resp.read()
        m = CSRF_RE.search(body)
        return m.group(1).decode() if m else None

    def _login(self):
        token = self._csrf(f'{self.server_url}/login')
        fields = {'username': self.username, 'password': self.password,
                  'submit': '', 'next': '/'}
        if token:
            fields['csrf_token'] = token
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(f'{self.server_url}/login', data=data)
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        with self._opener.open(req, timeout=15) as resp:
            final_url = resp.geturl()
            body = resp.read()
        if '/login' in final_url and CSRF_RE.search(body):
            return False
        return True

    def _session_valid(self):
        try:
            req = urllib.request.Request(f'{self.server_url}/')
            with self._opener.open(req, timeout=10) as resp:
                return '/login' not in resp.geturl()
        except Exception:
            return False

    # --- lifecycle ---
    def connect(self):
        # Reuse an existing valid session if we still have one.
        if self._opener is not None and self._session_valid():
            self.log('Reusing existing Calibre-web session.')
            return
        self._opener = self._new_opener()
        if not self._login():
            raise BackendError('Login failed: check username/password.')
        self.log('Logged in to Calibre-web.')

    def test_connection(self):
        try:
            self._opener = self._new_opener()
            req = urllib.request.Request(f'{self.server_url}/opds')
            if self.username:
                req.add_header('Authorization',
                               _basic_auth_header(self.username, self.password))
            with self._opener.open(req, timeout=10) as resp:
                if resp.status == 200:
                    return True, 'Connection OK — OPDS catalog reachable.'
                return False, f'Unexpected status: HTTP {resp.status}'
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, 'Authentication failed (HTTP 401) — check username/password.'
            if e.code == 429:
                return False, 'Server is rate-limiting requests (HTTP 429) — wait a minute and retry.'
            return False, f'Server error: HTTP {e.code}'
        except Exception as e:
            return False, f'Connection failed: {e}'

    # --- core operations ---
    def book_exists(self, title, authors):
        try:
            query = urllib.parse.quote(title)
            url = f'{self.server_url}/opds/search?query={query}'
            req = urllib.request.Request(url)
            if self.username:
                req.add_header('Authorization',
                               _basic_auth_header(self.username, self.password))
            with self._opener.open(req, timeout=15) as resp:
                data = resp.read()
            root = ET.fromstring(data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            tl = title.lower().strip()
            for entry in root.findall('atom:entry', ns):
                t = entry.find('atom:title', ns)
                if t is not None and t.text and t.text.lower().strip() == tl:
                    return True
            return False
        except Exception as e:
            self.log(f'Warning: duplicate check failed ({e}), uploading anyway.')
            return False

    def upload(self, filepath, filename):
        url = f'{self.server_url}/upload'
        token = self._csrf(f'{self.server_url}/')
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
            with self._opener.open(req, timeout=120) as resp:
                final_url = resp.geturl()
                status = resp.status
                resp_body = resp.read()
        except urllib.error.HTTPError as e:
            body_bytes = b''
            try:
                body_bytes = e.read()
            except Exception:
                pass
            snippet = _extract_error_text(body_bytes)
            if e.code == 422:
                raise BackendError(
                    f'Calibre-web rejected the file (HTTP 422){snippet}. '
                    f'Check that this format is allowed and uploads are enabled.')
            raise BackendError(f'server error (HTTP {e.code}){snippet}')

        if '/login' in final_url:
            raise BackendError('not logged in - server redirected to login page')
        if status not in (200, 201):
            snippet = _extract_error_text(resp_body)
            raise BackendError(f'server rejected upload (HTTP {status}){snippet}')
        return f'HTTP {status}'

    # --- shelves ---
    def find_book_id(self, title):
        try:
            query = urllib.parse.quote(title)
            url = f'{self.server_url}/opds/search?query={query}'
            req = urllib.request.Request(url)
            if self.username:
                req.add_header('Authorization',
                               _basic_auth_header(self.username, self.password))
            with self._opener.open(req, timeout=15) as resp:
                data = resp.read()
            root = ET.fromstring(data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            tl = title.lower().strip()
            for entry in root.findall('atom:entry', ns):
                t = entry.find('atom:title', ns)
                if t is None or not t.text or t.text.lower().strip() != tl:
                    continue
                for link in entry.findall('atom:link', ns):
                    m = DOWNLOAD_LINK_RE.search(link.get('href', ''))
                    if m:
                        return int(m.group(1))
            return None
        except Exception as e:
            self.log(f'Warning: could not find book id ({e}).')
            return None

    def _list_shelves(self):
        url = f'{self.server_url}/opds/shelfindex'
        req = urllib.request.Request(url)
        if self.username:
            req.add_header('Authorization',
                           _basic_auth_header(self.username, self.password))
        with self._opener.open(req, timeout=15) as resp:
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

    def ensure_shelf(self, shelf_name):
        shelves = self._list_shelves()
        sid = shelves.get(shelf_name.strip().lower())
        if sid is not None:
            return sid
        token = self._csrf(f'{self.server_url}/shelf/create')
        fields = {'title': shelf_name}
        if token:
            fields['csrf_token'] = token
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(f'{self.server_url}/shelf/create', data=data)
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        with self._opener.open(req, timeout=15) as resp:
            resp.read()
        self.log(f'Created shelf "{shelf_name}".')
        shelves = self._list_shelves()
        sid = shelves.get(shelf_name.strip().lower())
        if sid is None:
            raise BackendError(f'shelf "{shelf_name}" not found after creation')
        return sid

    def add_to_shelf(self, shelf_id, book_id):
        token = self._csrf(f'{self.server_url}/')
        req = urllib.request.Request(
            f'{self.server_url}/shelf/add/{shelf_id}/{book_id}', data=b'')
        req.add_header('X-Requested-With', 'XMLHttpRequest')
        if token:
            req.add_header('X-CSRFToken', token)
        try:
            with self._opener.open(req, timeout=15) as resp:
                return 'added' if resp.status in (200, 204) else 'added'
        except urllib.error.HTTPError as e:
            body = ''
            try:
                body = e.read().decode('utf-8', 'replace')
            except Exception:
                pass
            if e.code == 400 and 'already part of the shelf' in body.lower():
                return 'already'
            if e.code == 403:
                raise BackendError(
                    'not allowed to add to this shelf (check user shelf permissions)')
            raise BackendError(f'shelf add failed (HTTP {e.code}): {body[:200]}')
