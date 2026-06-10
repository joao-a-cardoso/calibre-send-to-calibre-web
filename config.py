# -*- coding: utf-8 -*-
# Copyright (C) 2026 João Cardoso
# License: GNU General Public License v3 (see LICENSE)

from qt.core import QWidget, QHBoxLayout, QLabel, QLineEdit, QCheckBox, QPushButton, QFormLayout
from calibre.utils.config import JSONConfig

load_translations()

prefs = JSONConfig('plugins/send_to_calibre_web')

prefs.defaults['server_url']   = 'http://localhost:8083'
prefs.defaults['username']     = ''
prefs.defaults['password']     = ''
prefs.defaults['verify_ssl']   = True
prefs.defaults['format_order'] = 'epub,mobi,azw3,fb2,pdf'
prefs.defaults['add_to_shelf'] = False
prefs.defaults['shelf_name']   = ''


class ConfigWidget(QWidget):

    def __init__(self):
        QWidget.__init__(self)
        self.layout = QFormLayout()
        self.setLayout(self.layout)

        self.server_url = QLineEdit(self)
        self.server_url.setText(prefs['server_url'])
        self.layout.addRow(QLabel(_('Server URL:')), self.server_url)

        self.username = QLineEdit(self)
        self.username.setText(prefs['username'])
        self.layout.addRow(QLabel(_('Username:')), self.username)

        self.password = QLineEdit(self)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setText(prefs['password'])
        self.layout.addRow(QLabel(_('Password:')), self.password)

        self.verify_ssl = QCheckBox(self)
        self.verify_ssl.setChecked(prefs['verify_ssl'])
        self.layout.addRow(QLabel(_('Verify SSL certificate:')), self.verify_ssl)

        self.format_order = QLineEdit(self)
        self.format_order.setText(prefs['format_order'])
        self.layout.addRow(QLabel(_('Format preference (comma separated):')), self.format_order)

        self.add_to_shelf = QCheckBox(self)
        self.add_to_shelf.setChecked(prefs['add_to_shelf'])
        self.layout.addRow(QLabel(_('Add sent books to a shelf:')), self.add_to_shelf)

        self.shelf_name = QLineEdit(self)
        self.shelf_name.setText(prefs['shelf_name'])
        self.shelf_name.setPlaceholderText(_('empty = use current library name'))
        self.layout.addRow(QLabel(_('Shelf name:')), self.shelf_name)

        self.test_button = QPushButton(_('Test connection'), self)
        self.test_button.clicked.connect(self.test_connection)
        self.test_result = QLabel('')
        self.test_result.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.test_button)
        row.addWidget(self.test_result, 1)
        self.layout.addRow(row)

    def test_connection(self):
        import urllib.request
        import urllib.error
        import base64
        import ssl

        url = str(self.server_url.text()).rstrip('/')
        username = str(self.username.text())
        password = str(self.password.text())
        verify_ssl = self.verify_ssl.isChecked()

        if not url:
            self.test_result.setText(_('✗ Enter a server URL first.'))
            self.test_result.setStyleSheet('color: red;')
            return

        self.test_result.setText(_('Testing…'))
        self.test_result.setStyleSheet('')
        self.test_button.setEnabled(False)
        try:
            if not verify_ssl:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
            else:
                opener = urllib.request.build_opener()

            req = urllib.request.Request(f'{url}/opds')
            if username:
                creds = base64.b64encode(f'{username}:{password}'.encode()).decode()
                req.add_header('Authorization', f'Basic {creds}')

            with opener.open(req, timeout=10) as resp:
                status = resp.status
            if status == 200:
                self.test_result.setText(_('✓ Connection OK — OPDS catalog reachable.'))
                self.test_result.setStyleSheet('color: green;')
            else:
                self.test_result.setText(_('✗ Unexpected status: HTTP %d') % status)
                self.test_result.setStyleSheet('color: red;')
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self.test_result.setText(_('✗ Authentication failed (HTTP 401) — check username/password.'))
            else:
                self.test_result.setText(_('✗ Server error: HTTP %d') % e.code)
            self.test_result.setStyleSheet('color: red;')
        except Exception as e:
            self.test_result.setText(_('✗ Connection failed: %s') % e)
            self.test_result.setStyleSheet('color: red;')
        finally:
            self.test_button.setEnabled(True)

    def commit(self):
        prefs['server_url']   = str(self.server_url.text()).rstrip('/')
        prefs['username']     = str(self.username.text())
        prefs['password']     = str(self.password.text())
        prefs['verify_ssl']   = self.verify_ssl.isChecked()
        prefs['format_order'] = str(self.format_order.text())
        prefs['add_to_shelf'] = self.add_to_shelf.isChecked()
        prefs['shelf_name']   = str(self.shelf_name.text()).strip()
