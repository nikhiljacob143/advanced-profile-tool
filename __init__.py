# -*- coding: utf-8 -*-
"""Advanced Profile Tool — QGIS plugin entry point.

Copyright (C) 2026 Nikhil Jacob
Licensed under GNU GPL v2 or later.
"""


def classFactory(iface):
    """Load the plugin. Deferred import keeps startup light."""
    from .plugin import AdvancedProfileToolPlugin
    return AdvancedProfileToolPlugin(iface)
