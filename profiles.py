# -*- coding: utf-8 -*-
# Copyright (C) 2026 João Cardoso
# License: GNU General Public License v3 (see LICENSE)

"""Named connection profiles for Send to Calibre-web.

A profile is a self-contained config dict: its own backend, server, credentials,
format preference, and shelf settings. The plugin stores a list of profiles plus
the name of the active (default) one. This module owns the data model and the
one-time migration from the original flat single-server prefs, so the UI and the
action code never touch the raw pref keys directly.
"""

#: Keys that make up a profile (besides 'name').
PROFILE_FIELDS = (
    'backend', 'server_url', 'username', 'password',
    'verify_ssl', 'format_order', 'add_to_shelf', 'shelf_name',
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
}


def new_profile(name, **overrides):
    """Build a profile dict with defaults, applying any overrides."""
    p = {'name': name}
    p.update(DEFAULTS)
    for k, v in overrides.items():
        if k in PROFILE_FIELDS or k == 'name':
            p[k] = v
    return p


def migrate(prefs):
    """Ensure ``prefs`` holds a profiles list and an active_profile.

    On first run after upgrading from the flat single-server layout, wrap the
    existing settings into one profile named "Default". Idempotent: does nothing
    if profiles already exist.
    """
    if prefs.get('profiles'):
        return

    # Pull whatever the old flat keys held (JSONConfig returns defaults if set).
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
    """Return the list of profile dicts (migrating if needed)."""
    migrate(prefs)
    return prefs['profiles']


def get_profile(prefs, name):
    """Return the profile with ``name``, or None."""
    for p in get_profiles(prefs):
        if p.get('name') == name:
            return p
    return None


def get_active_profile(prefs):
    """Return the active/default profile, falling back to the first one."""
    profiles = get_profiles(prefs)
    active = prefs.get('active_profile')
    p = get_profile(prefs, active) if active else None
    if p is None and profiles:
        p = profiles[0]
        prefs['active_profile'] = p['name']
    return p


def set_active_profile(prefs, name):
    """Set the active/default profile by name, if it exists."""
    if get_profile(prefs, name) is not None:
        prefs['active_profile'] = name


def save_profiles(prefs, profiles, active_name=None):
    """Persist the profiles list (and optionally the active name)."""
    prefs['profiles'] = profiles
    if active_name is not None:
        prefs['active_profile'] = active_name
    elif profiles and prefs.get('active_profile') not in [p['name'] for p in profiles]:
        prefs['active_profile'] = profiles[0]['name']


def profile_to_config(profile):
    """Extract the backend config dict (server/creds) from a profile."""
    return {
        'server_url': (profile.get('server_url') or '').rstrip('/'),
        'username': profile.get('username', ''),
        'password': profile.get('password', ''),
        'verify_ssl': profile.get('verify_ssl', True),
    }


def unique_name(profiles, base):
    """Return a profile name not already in use, e.g. 'Copy', 'Copy 2'."""
    existing = {p.get('name') for p in profiles}
    if base not in existing:
        return base
    i = 2
    while f'{base} {i}' in existing:
        i += 1
    return f'{base} {i}'
