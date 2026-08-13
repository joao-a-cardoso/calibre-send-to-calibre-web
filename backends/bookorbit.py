# -*- coding: utf-8 -*-
# Copyright (C) 2026 João Cardoso
# License: GNU General Public License v3 (see LICENSE)

"""BookOrbit backend driver — SKELETON.

BookOrbit (https://github.com/bookorbit/bookorbit) is a Docker-native,
self-hosted reading platform. Unlike Calibre-web's session+CSRF form login, it
authenticates with a JWT bearer token (it also supports OIDC/SSO). Books are
ingested via a browser upload endpoint, Book Dock, or direct filesystem copy;
this driver targets the HTTP upload path. Its OPDS catalog supports search
(by author, series, ISBN), which we reuse for duplicate detection.

==================  IMPORTANT  ==================
BookOrbit does not publish a documented REST API. The three spots marked
`# VERIFY:` below need confirming against a real instance by capturing the
requests in browser DevTools (Network tab → "Copy as cURL"):

  1. The login endpoint and how the JWT comes back (body field vs cookie).
  2. The upload endpoint path, the multipart file field name, and any extra
     form fields (e.g. target library id).
  3. The error-response shapes (duplicate, disallowed format, 401) so failures
     are reported clearly rather than as raw HTTP errors.

Everything else (OPDS duplicate check, bearer-token plumbing, the Backend
contract) is standard and implemented. Once the three items are verified, this
driver can be registered in backends/__init__.py.
=================================================
"""

import json
import ssl
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET

from calibre_plugins.send_to_calibre_web.backends.base import (
    AuthenticationError, Backend, BackendError, LookupResult, RemoteBook)


class BookOrbitBackend(Backend):

    key = 'bookorbit'
    name = 'BookOrbit'
    # OPDS search exists, so duplicate detection is supported.
    supports_duplicate_check = True
    # Collections/Smart Scopes exist but their write API is unverified; ship
    # without shelf support first and enable once the endpoints are confirmed.
    supports_shelves = False
    # Delete endpoint not yet verified; "Replace" disabled until then.
    supports_replace = False

    def __init__(self, config, log=None, shared_state=None):
        Backend.__init__(self, config, log=log, shared_state=shared_state)
        self.server_url = config['server_url'].rstrip('/')
        self.username = config.get('username', '')
        self.password = config.get('password', '')
        self.verify_ssl = config.get('verify_ssl', True)
        # Optional: which library to upload into (BookOrbit is multi-library).
        self.library_id = config.get('library_id', '')
        self._token = None
        self._opener = None

    # --- opener ---
    def _new_opener(self):
        handlers = []
        if not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        return urllib.request.build_opener(*handlers)

    def _auth_headers(self):
        """Bearer token header for authenticated requests."""
        if self._token:
            return {'Authorization': f'Bearer {self._token}'}
        return {}

    def _request(self, method, path, data=None, headers=None, timeout=30):
        url = path if path.startswith('http') else f'{self.server_url}{path}'
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        for k, v in self._auth_headers().items():
            req.add_header(k, v)
        return self._opener.open(req, timeout=timeout)

    # --- lifecycle ---
    def connect(self, validate=False):
        """Obtain a JWT and keep it for subsequent requests.

        # VERIFY (1): the login endpoint, request body, and where the token is
        # returned. The shape below is the common convention
        # (POST /api/auth/login with JSON, token in response JSON) and is a
        # placeholder until confirmed against a real instance.
        """
        self._opener = self._new_opener()
        login_url = f'{self.server_url}/api/auth/login'   # VERIFY
        payload = json.dumps({
            'username': self.username,     # VERIFY: 'username' vs 'email'
            'password': self.password,
        }).encode()
        try:
            req = urllib.request.Request(login_url, data=payload, method='POST')
            req.add_header('Content-Type', 'application/json')
            with self._opener.open(req, timeout=20) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403):
                raise BackendError('Login failed: check username/password.')
            raise BackendError(f'login error (HTTP {e.code})')
        try:
            obj = json.loads(body.decode('utf-8', 'replace'))
        except Exception:
            raise BackendError('unexpected login response (not JSON)')
        # VERIFY: the token field name. Common candidates handled defensively.
        token = (obj.get('token') or obj.get('access_token')
                 or obj.get('accessToken') or obj.get('jwt'))
        if not token:
            raise BackendError('login succeeded but no token field found '
                               '(VERIFY the token field name)')
        self._token = token
        self.log('Logged in to BookOrbit.')

    def test_connection(self):
        try:
            self.connect()
            return True, 'Connection OK — logged in to BookOrbit.'
        except BackendError as e:
            return False, str(e)
        except Exception as e:
            return False, f'Connection failed: {e}'

    # --- duplicate lookup via OPDS (placeholder until BookOrbit API verified) ---
    def find_book(self, identity):
        try:
            query = urllib.parse.quote(identity.title)
            url = f'{self.server_url}/opds/search?query={query}'   # VERIFY path
            with self._request('GET', url, headers={}, timeout=15) as resp:
                data = resp.read()
            root = ET.fromstring(data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            wanted = identity.title.casefold().strip()
            matches = []
            for entry in root.findall('atom:entry', ns):
                t = entry.find('atom:title', ns)
                if t is None or not t.text or t.text.casefold().strip() != wanted:
                    continue
                authors = []
                for author in entry.findall('atom:author', ns):
                    name = author.find('atom:name', ns)
                    if name is not None and name.text:
                        authors.append(name.text.strip())
                matches.append(RemoteBook(None, t.text.strip(), tuple(authors), {}))
            if not matches:
                return LookupResult.not_found()
            if len(matches) == 1:
                return LookupResult.found(matches[0])
            return LookupResult.ambiguous(
                f'{len(matches)} BookOrbit entries share this title')
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise AuthenticationError('BookOrbit authentication failed (HTTP 401)')
            self.log(f'Warning: duplicate lookup failed (HTTP {e.code}).')
            return LookupResult.unknown(f'HTTP {e.code}')
        except urllib.error.URLError:
            raise
        except ET.ParseError as e:
            self.log(f'Warning: duplicate lookup failed ({e}).')
            return LookupResult.unknown(f'invalid OPDS response: {e}')

    # --- upload ---
    def upload(self, filepath, filename):
        """Upload a book via BookOrbit's browser-upload endpoint.

        # VERIFY (2): the upload URL, the multipart file field name, and any
        # required form fields (target library id, etc.). The structure below
        # is a standard multipart POST and is a placeholder until confirmed.
        """
        # VERIFY: endpoint. Likely scoped to a library, e.g.
        #   POST /api/libraries/<library_id>/books   or   /api/upload
        if self.library_id:
            url = f'{self.server_url}/api/libraries/{self.library_id}/upload'  # VERIFY
        else:
            url = f'{self.server_url}/api/upload'  # VERIFY

        with open(filepath, 'rb') as f:
            file_data = f.read()

        # VERIFY: file field name (here 'file') and any extra fields.
        safe = filename.replace('\r', '').replace('\n', '').replace('"', '\\"')
        boundary = '----SendToCalibreWebBoundary'
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="{safe}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'
        ).encode() + file_data + f'\r\n--{boundary}--\r\n'.encode()

        headers = {
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Content-Length': str(len(body)),
        }
        try:
            with self._request('POST', url, data=body, headers=headers, timeout=300) as resp:
                status = resp.status
                resp.read()
        except urllib.error.HTTPError as e:
            # VERIFY (3): map BookOrbit's real error bodies. Documented behaviour:
            #   - duplicate name on disk -> rejected
            #   - format not in library's allowed formats -> rejected
            #   - 500 MB per-file ceiling
            body_text = ''
            try:
                body_text = e.read().decode('utf-8', 'replace')
            except Exception:
                pass
            hint = f': {body_text[:200]}' if body_text else ''
            if e.code in (401, 403):
                raise BackendError('not authorized to upload (check user '
                                   'library_upload permission)')
            if e.code == 409:
                raise BackendError(f'duplicate / name conflict (HTTP 409){hint}')
            if e.code in (413, 422):
                raise BackendError(f'file rejected (HTTP {e.code}){hint}')
            raise BackendError(f'upload failed (HTTP {e.code}){hint}')
        if status not in (200, 201):
            raise BackendError(f'upload returned HTTP {status}')
        return f'HTTP {status}'

    # --- shelves: not enabled until the Collections write API is verified ---
    def ensure_shelf(self, shelf_name):
        raise NotImplementedError

    def add_to_shelf(self, shelf_id, book_id):
        raise NotImplementedError
