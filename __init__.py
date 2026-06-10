# -*- coding: utf-8 -*-
# Copyright (C) 2026 Claude (Anthropic) and João Cardoso
# License: GNU General Public License v3 (see LICENSE)

from calibre.customize import InterfaceActionBase

class SendToCalibreWebPlugin(InterfaceActionBase):
    name                   = 'Send to Calibre-web'
    description            = 'Send selected books to a Calibre-web server'
    supported_platforms    = ['windows', 'osx', 'linux']
    author                 = 'Claude and João Cardoso'
    version                = (1, 3, 0)
    minimum_calibre_version = (6, 0, 0)

    actual_plugin = 'calibre_plugins.send_to_calibre_web.action:SendToCalibreWebAction'

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.send_to_calibre_web.config import ConfigWidget
        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.commit()
