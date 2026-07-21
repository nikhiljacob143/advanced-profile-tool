# -*- coding: utf-8 -*-
"""Plugin bootstrap: menu, toolbar, dock, processing provider.

Copyright (C) 2026 Nikhil Jacob — GPL v2 or later.
"""
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .constants import PLUGIN_NAME


class AdvancedProfileToolPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.provider = None
        self.actions = []
        self.icon = QIcon(os.path.join(os.path.dirname(__file__), "icons",
                                       "advanced_profile_tool.png"))

    # ------------------------------------------------------------------ #
    def initGui(self):
        act_open = QAction(self.icon, PLUGIN_NAME,
                           self.iface.mainWindow())
        act_open.setToolTip("Open the Advanced Profile Tool panel "
                            "(Ctrl+Alt+A)")
        act_open.setShortcut("Ctrl+Alt+A")
        act_open.triggered.connect(self.show_dock)
        self.iface.addToolBarIcon(act_open)
        self.iface.addPluginToMenu(PLUGIN_NAME, act_open)
        self.actions.append(act_open)

        act_help = QAction("Help", self.iface.mainWindow())
        act_help.triggered.connect(self._open_help)
        self.iface.addPluginToMenu(PLUGIN_NAME, act_help)
        self.actions.append(act_help)

        act_about = QAction("About", self.iface.mainWindow())
        act_about.triggered.connect(self._about)
        self.iface.addPluginToMenu(PLUGIN_NAME, act_about)
        self.actions.append(act_about)

        try:
            from .processing.provider import AdvancedProfileProvider
            from qgis.core import QgsApplication
            self.provider = AdvancedProfileProvider()
            QgsApplication.processingRegistry().addProvider(self.provider)
        except Exception as e:                        # noqa: BLE001
            from qgis.core import Qgis, QgsMessageLog
            QgsMessageLog.logMessage(
                f"Processing provider failed to load: {e}",
                "AdvancedProfileTool", Qgis.MessageLevel.Warning)

    def unload(self):
        if self.dock is not None:
            self.dock.cleanup()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        for act in self.actions:
            self.iface.removePluginMenu(PLUGIN_NAME, act)
            self.iface.removeToolBarIcon(act)
        self.actions = []
        if self.provider is not None:
            from qgis.core import QgsApplication
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    # ------------------------------------------------------------------ #
    def show_dock(self):
        if self.dock is None:
            from .ui.dock_widget import AdvancedProfileDock
            self.dock = AdvancedProfileDock(self.iface,
                                            self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.show()
        self.dock.raise_()

    def _open_help(self):
        from .ui.dialogs import open_help
        open_help()

    def _about(self):
        from .ui.dialogs import AboutDialog
        AboutDialog(self.iface.mainWindow()).exec()
