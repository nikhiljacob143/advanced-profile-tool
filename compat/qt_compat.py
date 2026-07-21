# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Qt import shim.

Inside QGIS, qgis.PyQt is the supported way to access Qt (it maps to
Qt5 or Qt6 depending on the build, which keeps this plugin compatible
with both). When the module is imported outside QGIS (headless tests,
tooling), QtCore/QtGui/QtWidgets are set to None and HAS_QT is False so
UI-adjacent pure code remains importable.

Usage:
    from advanced_profile_tool.compat.qt_compat import QtCore, HAS_QT
"""
try:
    from qgis.PyQt import QtCore, QtGui, QtWidgets
    HAS_QT = True
    QT_SOURCE = "qgis.PyQt"
except ImportError:
    QtCore = QtGui = QtWidgets = None
    HAS_QT = False
    QT_SOURCE = ""

__all__ = ["QtCore", "QtGui", "QtWidgets", "HAS_QT", "QT_SOURCE"]
