# -*- coding: utf-8 -*-
"""About and helper dialogs.

Copyright (C) 2026 Nikhil Jacob — GPL v2 or later.
"""
import os

from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (QDialog, QDialogButtonBox, QLabel,
                                 QVBoxLayout)

from ..constants import PLUGIN_NAME, PLUGIN_VERSION


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {PLUGIN_NAME}")
        lay = QVBoxLayout(self)
        text = QLabel(
            f"<h3>{PLUGIN_NAME} {PLUGIN_VERSION}</h3>"
            "<p>Alignment-based cross-sections, multi-DEM profiles, "
            "terrain comparison, volumes and CAD/report exports for "
            "QGIS.</p>"
            "<p>© 2026 Nikhil Jacob — GNU GPL v2 or later.<br>"
            "Bundled: dxfwrite 1.2.1 (MIT, © Manfred Moitzi).</p>")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(text)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        lay.addWidget(btns)


def open_help():
    """Open the bundled help page in the default browser."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "help", "user_guide.html")
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))
