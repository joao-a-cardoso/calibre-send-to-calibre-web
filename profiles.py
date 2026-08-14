# -*- coding: utf-8 -*-
# Copyright (C) 2026 João Cardoso
# License: GNU General Public License v3 (see LICENSE)

"""Named connection profiles for Send to Calibre-web.

Profiles now carry an internal stable id and a connection revision.  Session
reuse is keyed by those values rather than by credentials, and the revision is
bumped only when connection-relevant settings change.
"""

import uuid

PROFILE_FIELDS = (
    'backend', 'server_url', 'username', 'password',
    'verify_ssl', 'format_order', 'add_to_shelf', 'shelf_name',
    'duplicate_policy', 'allow_delete',
)

CONNECTION_FIELDS = (
    'backend', 'server_url', 'username', 'password', 'verify_ssl',
)

DEFAULTS = {
    'backend': 'calibre-web',
    'server_url': '',
    'username': '',
    'password': '',
    'verify_ssl': True,
    'format_order': 'epub,mobi,azw3,fb2,pdf',
    'add_to_shelf': False,
    'shelf_name': '',
    'duplicate_policy': 'keep',
    # Explicit opt-in for exposing destructive Remove actions for this profile.
    'allow_delete': False,
}


def _new_id():
    return uuid.uuid4().hex


def _ensure_metadata(profile):
    changed = False
    if not profile.get('id'):
        profile['id'] = _new_id()
        changed = True
    try:
        revision = int(profile.get('connection_revision', 0))
    except (TypeError, ValueError):
        revision = 0
    if profile.get('connection_revision') != revision:
        profile['connection_revision'] = revision
        changed = True
    elif 'connection_revision' not in profile:
        profile['connection_revision'] = revision
        changed = True
    return changed


def new_profile(name, **overrides):
    p = {
        'name': name,
        'id': _new_id(),
        'connection_revision': 0,
    }
    p.update(DEFAULTS)
    for k, v in overrides.items():
        if k in PROFILE_FIELDS or k in ('name', 'id', 'connection_revision'):
            p[k] = v
    _ensure_metadata(p)
    return p


def migrate(prefs):
    """Ensure profile layout and internal metadata exist; idempotent."""
    profiles = prefs.get('profiles') or []
    if profiles:
        changed = False
        for profile in profiles:
            changed = _ensure_metadata(profile) or changed
            for key, default in DEFAULTS.items():
                if key not in profile:
                    profile[key] = default
                    changed = True
        if changed:
            prefs['profiles'] = profiles
        active = prefs.get('active_profile')
        if not active or not any(p.get('name') == active for p in profiles):
            prefs['active_profile'] = profiles[0]['name']
        return

    legacy = new_profile(
        'Default',
        backend=prefs.get('backend', DEFAULTS['backend']),
        server_url=prefs.get('server_url', DEFAULTS['server_url']),
        username=prefs.get('username', DEFAULTS['username']),
        password=prefs.get('password', DEFAULTS['password']),
        verify_ssl=prefs.get('verify_ssl', DEFAULTS['verify_ssl']),
        format_order=prefs.get('format_order', DEFAULTS['format_order']),
        add_to_shelf=prefs.get('add_to_shelf', DEFAULTS['add_to_shelf']),
        shelf_name=prefs.get('shelf_name', DEFAULTS['shelf_name']),
    )
    prefs['profiles'] = [legacy]
    prefs['active_profile'] = 'Default'


def get_profiles(prefs):
    migrate(prefs)
    return prefs['profiles']


def get_profile(prefs, name):
    for p in get_profiles(prefs):
        if p.get('name') == name:
            return p
    return None


def get_active_profile(prefs):
    profiles = get_profiles(prefs)
    active = prefs.get('active_profile')
    p = get_profile(prefs, active) if active else None
    if p is None and profiles:
        p = profiles[0]
        prefs['active_profile'] = p['name']
    return p


def set_active_profile(prefs, name):
    if get_profile(prefs, name) is not None:
        prefs['active_profile'] = name


def _connection_changed(old, new):
    return any(old.get(k, DEFAULTS.get(k)) != new.get(k, DEFAULTS.get(k))
               for k in CONNECTION_FIELDS)


def save_profiles(prefs, profiles, active_name=None):
    """Persist profiles and bump connection revisions when connection config changes."""
    migrate(prefs)
    old_by_id = {p.get('id'): p for p in (prefs.get('profiles') or []) if p.get('id')}

    for profile in profiles:
        _ensure_metadata(profile)
        old = old_by_id.get(profile['id'])
        if old is not None:
            old_revision = int(old.get('connection_revision', 0) or 0)
            profile['connection_revision'] = (
                old_revision + 1 if _connection_changed(old, profile) else old_revision)

    prefs['profiles'] = profiles
    if active_name is not None:
        prefs['active_profile'] = active_name
    elif profiles and prefs.get('active_profile') not in [p['name'] for p in profiles]:
        prefs['active_profile'] = profiles[0]['name']


def profile_to_config(profile):
    return {
        'server_url': (profile.get('server_url') or '').rstrip('/'),
        'username': profile.get('username', ''),
        'password': profile.get('password', ''),
        'verify_ssl': profile.get('verify_ssl', True),
    }


def unique_name(profiles, base):
    existing = {p.get('name') for p in profiles}
    if base not in existing:
        return base
    i = 2
    while f'{base} {i}' in existing:
        i += 1
    return f'{base} {i}'
