# -*- coding: utf-8 -*-
# Copyright (C) 2026 João Cardoso
# License: GNU General Public License v3 (see LICENSE)

from qt.core import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit,
                     QCheckBox, QPushButton, QFormLayout, QComboBox,
                     QListWidget, QGroupBox, QFrame, QInputDialog, QMessageBox)
from calibre.utils.config import JSONConfig

import calibre_plugins.send_to_calibre_web.profiles as P

load_translations()

prefs = JSONConfig('plugins/send_to_calibre_web')

# Legacy flat defaults (kept so migration can read them on first upgrade).
# NOTE: Calibre's JSONConfig stores these in plain text on disk, as it has no
# secret store. The password is therefore not encrypted at rest — the same
# limitation as Calibre's own server/device passwords. Documented in the README.
prefs.defaults['backend']      = 'calibre-web'
prefs.defaults['server_url']   = 'http://localhost:8083'
prefs.defaults['username']     = ''
prefs.defaults['password']     = ''
prefs.defaults['verify_ssl']   = True
prefs.defaults['format_order'] = 'epub,mobi,azw3,fb2,pdf'
prefs.defaults['add_to_shelf'] = False
prefs.defaults['shelf_name']   = ''
# New profile-based layout.
prefs.defaults['profiles']        = []
prefs.defaults['active_profile']  = ''


class ConfigWidget(QWidget):

    def __init__(self):
        QWidget.__init__(self)
        P.migrate(prefs)
        # Work on a copy so Cancel/closing without save doesn't persist.
        import copy
        self._profiles = copy.deepcopy(P.get_profiles(prefs))
        self._active = prefs.get('active_profile') or (
            self._profiles[0]['name'] if self._profiles else '')
        self._current_index = -1

        self.setMinimumWidth(720)
        root = QHBoxLayout(self)

        # --- Left: profile list + buttons ---
        left = QVBoxLayout()
        left.addWidget(QLabel('<b>' + _('Profiles') + '</b>'))
        self.list = QListWidget(self)
        self.list.setMaximumWidth(180)
        self.list.currentRowChanged.connect(self._on_select)
        left.addWidget(self.list)
        brow = QHBoxLayout()
        self.btn_add = QPushButton(_('Add'))
        self.btn_remove = QPushButton(_('Remove'))
        self.btn_rename = QPushButton(_('Rename'))
        self.btn_dup = QPushButton(_('Duplicate'))
        for b in (self.btn_add, self.btn_remove, self.btn_rename, self.btn_dup):
            brow.addWidget(b)
        self.btn_add.clicked.connect(self._add)
        self.btn_remove.clicked.connect(self._remove)
        self.btn_rename.clicked.connect(self._rename)
        self.btn_dup.clicked.connect(self._duplicate)
        left.addLayout(brow)
        root.addLayout(left)

        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(line)

        # --- Right: detail editor ---
        right = QVBoxLayout()
        self.group = QGroupBox(self)
        form = QFormLayout(self.group)
        fw = 300

        from calibre_plugins.send_to_calibre_web.backends import backend_choices
        self.backend = QComboBox(self)
        self._backend_keys = []
        for key, label in backend_choices():
            self.backend.addItem(label, key)
            self._backend_keys.append(key)
        self.backend.setMinimumWidth(fw)
        form.addRow(QLabel(_('Backend:')), self.backend)

        self.server_url = QLineEdit(self); self.server_url.setMinimumWidth(fw)
        form.addRow(QLabel(_('Server URL:')), self.server_url)
        self.username = QLineEdit(self); self.username.setMinimumWidth(fw)
        form.addRow(QLabel(_('Username:')), self.username)
        self.password = QLineEdit(self); self.password.setMinimumWidth(fw)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(QLabel(_('Password:')), self.password)
        self.verify_ssl = QCheckBox(self)
        form.addRow(QLabel(_('Verify SSL certificate:')), self.verify_ssl)
        self.format_order = QLineEdit(self); self.format_order.setMinimumWidth(fw)
        form.addRow(QLabel(_('Format preference (comma separated):')), self.format_order)
        self.duplicate_policy = QComboBox(self)
        self.duplicate_policy.setMinimumWidth(fw)
        form.addRow(QLabel(_('If book already exists:')), self.duplicate_policy)
        # Destructive options are capability-gated by the selected backend.
        self.backend.currentIndexChanged.connect(self._refresh_policy_options)
        self.backend.currentIndexChanged.connect(self._refresh_delete_option)
        self.allow_delete = QCheckBox(self)
        form.addRow(QLabel(_('Allow removing books from server:')), self.allow_delete)
        self.add_to_shelf = QCheckBox(self)
        form.addRow(QLabel(_('Add sent books to a shelf:')), self.add_to_shelf)
        self.shelf_name = QLineEdit(self); self.shelf_name.setMinimumWidth(fw)
        self.shelf_name.setPlaceholderText(_('empty = use current library name'))
        form.addRow(QLabel(_('Shelf name:')), self.shelf_name)

        trow = QHBoxLayout()
        self.test_button = QPushButton(_('Test connection'))
        self.test_button.clicked.connect(self.test_connection)
        self.test_result = QLabel(''); self.test_result.setWordWrap(True)
        trow.addWidget(self.test_button); trow.addWidget(self.test_result, 1)
        form.addRow(trow)
        right.addWidget(self.group)

        drow = QHBoxLayout()
        self.default_label = QLabel('')
        self.btn_set_default = QPushButton(_('Set as default'))
        self.btn_set_default.clicked.connect(self._set_default)
        drow.addWidget(self.default_label, 1)
        drow.addWidget(self.btn_set_default)
        right.addLayout(drow)
        right.addStretch()
        root.addLayout(right)

        self._reload_list()

    # --- list/detail sync ---
    def _reload_list(self, select_name=None):
        self.list.blockSignals(True)
        self.list.clear()
        for p in self._profiles:
            self.list.addItem(p['name'])
        self.list.blockSignals(False)
        target = select_name or self._active
        idx = 0
        for i, p in enumerate(self._profiles):
            if p['name'] == target:
                idx = i
                break
        if self._profiles:
            self.list.setCurrentRow(idx)
        self._update_default_label()

    def _flush_current(self):
        """Save the editor fields back into the current profile dict."""
        if 0 <= self._current_index < len(self._profiles):
            p = self._profiles[self._current_index]
            p['backend'] = self.backend.currentData()
            p['server_url'] = str(self.server_url.text()).rstrip('/')
            p['username'] = str(self.username.text())
            p['password'] = str(self.password.text())
            p['verify_ssl'] = self.verify_ssl.isChecked()
            p['format_order'] = str(self.format_order.text())
            p['duplicate_policy'] = self.duplicate_policy.currentData()
            p['allow_delete'] = self.allow_delete.isChecked() if self.allow_delete.isEnabled() else False
            p['add_to_shelf'] = self.add_to_shelf.isChecked()
            p['shelf_name'] = str(self.shelf_name.text()).strip()

    def _on_select(self, row):
        # Persist the profile we're leaving, then load the new one.
        self._flush_current()
        self._current_index = row
        if not (0 <= row < len(self._profiles)):
            return
        p = self._profiles[row]
        self.group.setTitle(_('Editing profile: %s') % p['name'])
        key = p.get('backend', 'calibre-web')
        if key in self._backend_keys:
            self.backend.setCurrentIndex(self._backend_keys.index(key))
        self.server_url.setText(p.get('server_url', ''))
        self.username.setText(p.get('username', ''))
        self.password.setText(p.get('password', ''))
        self.verify_ssl.setChecked(p.get('verify_ssl', True))
        self.format_order.setText(p.get('format_order', 'epub,mobi,azw3,fb2,pdf'))
        # Rebuild policy choices for this profile's backend, then select the
        # saved policy (falling back to 'keep' if it's no longer offered).
        self._refresh_policy_options(select=p.get('duplicate_policy', 'keep'))
        self._refresh_delete_option(checked=p.get('allow_delete', False))
        self.add_to_shelf.setChecked(p.get('add_to_shelf', False))
        self.shelf_name.setText(p.get('shelf_name', ''))
        self.test_result.setText('')

    def _refresh_policy_options(self, *args, select=None):
        """Populate the duplicate-policy dropdown based on whether the
        currently selected backend supports replacing (deleting) books."""
        from calibre_plugins.send_to_calibre_web.backends import get_backend_class
        key = self.backend.currentData() or 'calibre-web'
        backend_cls = get_backend_class(key)
        can_replace = getattr(backend_cls, 'supports_replace', False)

        # Preserve the current selection if caller didn't specify one.
        if select is None:
            select = self.duplicate_policy.currentData() or 'keep'

        self.duplicate_policy.blockSignals(True)
        self.duplicate_policy.clear()
        self.duplicate_policy.addItem(_('Keep existing (skip)'), 'keep')
        if can_replace:
            self.duplicate_policy.addItem(
                _('Replace existing (delete then upload)'), 'replace')
            self.duplicate_policy.addItem(
                _('Always ask before replacing'), 'ask')
        # Select the requested value if available, else default to keep.
        idx = self.duplicate_policy.findData(select)
        self.duplicate_policy.setCurrentIndex(idx if idx >= 0 else 0)
        self.duplicate_policy.blockSignals(False)

    def _refresh_delete_option(self, *args, checked=None):
        """Enable remote-removal opt-in only for delete-capable backends."""
        from calibre_plugins.send_to_calibre_web.backends import get_backend_class
        key = self.backend.currentData() or 'calibre-web'
        backend_cls = get_backend_class(key)
        can_delete = getattr(backend_cls, 'supports_delete', False)
        self.allow_delete.setEnabled(can_delete)
        if not can_delete:
            self.allow_delete.setChecked(False)
        elif checked is not None:
            self.allow_delete.setChecked(bool(checked))

    def _update_default_label(self):
        self.default_label.setText(
            _('Default profile (used on toolbar click): %s') % ('<b>%s</b>' % self._active))

    # --- buttons ---
    def _add(self):
        name = P.unique_name(self._profiles, 'New profile')
        self._flush_current()
        self._profiles.append(P.new_profile(name))
        if not self._active:
            self._active = name
        self._reload_list(select_name=name)

    def _remove(self):
        if len(self._profiles) <= 1:
            QMessageBox.information(self, _('Cannot remove'),
                                    _('At least one profile is required.'))
            return
        row = self.list.currentRow()
        if not (0 <= row < len(self._profiles)):
            return
        name = self._profiles[row]['name']
        if QMessageBox.question(self, _('Remove profile'),
                _('Remove profile "%s"?') % name) != QMessageBox.StandardButton.Yes:
            return
        del self._profiles[row]
        self._current_index = -1
        if self._active == name:
            self._active = self._profiles[0]['name']
        self._reload_list()

    def _rename(self):
        row = self.list.currentRow()
        if not (0 <= row < len(self._profiles)):
            return
        old = self._profiles[row]['name']
        new, ok = QInputDialog.getText(self, _('Rename profile'),
                                       _('New name:'), text=old)
        new = (new or '').strip()
        if not ok or not new or new == old:
            return
        if any(p['name'] == new for p in self._profiles):
            QMessageBox.information(self, _('Name in use'),
                                    _('A profile with that name already exists.'))
            return
        self._flush_current()
        self._profiles[row]['name'] = new
        if self._active == old:
            self._active = new
        self._reload_list(select_name=new)

    def _duplicate(self):
        row = self.list.currentRow()
        if not (0 <= row < len(self._profiles)):
            return
        self._flush_current()
        src = self._profiles[row]
        name = P.unique_name(self._profiles, src['name'] + ' copy')
        # A duplicate is a new profile identity. Copy user settings, but give it
        # a fresh internal id/revision so authenticated state is never shared
        # accidentally with the source profile.
        dup = P.new_profile(name, **{k: src.get(k) for k in P.PROFILE_FIELDS})
        self._profiles.append(dup)
        self._reload_list(select_name=dup['name'])

    def _set_default(self):
        row = self.list.currentRow()
        if 0 <= row < len(self._profiles):
            self._active = self._profiles[row]['name']
            self._update_default_label()

    # --- test connection (uses the selected profile via its backend) ---
    def test_connection(self):
        self._flush_current()
        row = self.list.currentRow()
        if not (0 <= row < len(self._profiles)):
            return
        p = self._profiles[row]
        url = (p.get('server_url') or '').rstrip('/')
        if not url:
            self.test_result.setText(_('✗ Enter a server URL first.'))
            self.test_result.setStyleSheet('color: red;')
            return
        self.test_result.setText(_('Testing…'))
        self.test_result.setStyleSheet('')
        self.test_button.setEnabled(False)
        try:
            from calibre_plugins.send_to_calibre_web.backends import get_backend_class
            cfg = P.profile_to_config(p)
            backend = get_backend_class(p.get('backend', 'calibre-web'))(cfg)
            ok, msg = backend.test_connection()
            self.test_result.setText(('✓ ' if ok else '✗ ') + msg)
            self.test_result.setStyleSheet('color: %s;' % ('green' if ok else 'red'))
        except Exception as e:
            self.test_result.setText(_('✗ Connection failed: %s') % e)
            self.test_result.setStyleSheet('color: red;')
        finally:
            self.test_button.setEnabled(True)

    def commit(self):
        self._flush_current()
        P.save_profiles(prefs, self._profiles, active_name=self._active)
