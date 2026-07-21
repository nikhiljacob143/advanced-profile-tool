# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""QGIS availability/version shim.

Lets pure-Python modules and the test-suite check for QGIS without
importing it. HAS_QGIS is resolved with importlib.util.find_spec so the
check itself never triggers a qgis import side-effect.
"""
import importlib.util

HAS_QGIS = importlib.util.find_spec("qgis") is not None


def qgis_version_int():
    """QGIS version as an integer (e.g. 34400 for 3.44.0).

    Returns 0 when QGIS is not available or the version cannot be read.
    """
    if not HAS_QGIS:
        return 0
    try:
        from qgis.core import Qgis
        return int(Qgis.QGIS_VERSION_INT)
    except Exception:
        return 0
