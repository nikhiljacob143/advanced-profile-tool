# -*- coding: utf-8 -*-
"""Item delegates for the DEM table: colour picker, combo boxes and
numeric editors so every surface can be styled individually.

Copyright (C) 2026 Nikhil Jacob — GPL v2 or later.
"""
import logging
from qgis.PyQt.QtCore import Qt

_LOG = logging.getLogger(__name__)
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QColorDialog, QComboBox, QDoubleSpinBox,
                                 QSpinBox, QStyledItemDelegate)


class ColorDelegate(QStyledItemDelegate):
    """Double-click opens the QGIS colour dialog; the cell shows a swatch."""

    def createEditor(self, parent, option, index):
        current = QColor(str(index.data(Qt.ItemDataRole.EditRole) or "#1976D2"))
        color = QColorDialog.getColor(
            current, parent, "Surface colour",
            QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if color.isValid():
            index.model().setData(index, color.name(), Qt.ItemDataRole.EditRole)
        return None                     # no inline editor widget


class ComboDelegate(QStyledItemDelegate):
    """Fixed-choice combo editor (interpolation, line style)."""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.items = list(items)

    def createEditor(self, parent, option, index):
        cbo = QComboBox(parent)
        cbo.addItems(self.items)
        return cbo

    def setEditorData(self, editor, index):
        val = str(index.data(Qt.ItemDataRole.EditRole) or "")
        i = editor.findText(val)
        editor.setCurrentIndex(max(i, 0))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class SpinDelegate(QStyledItemDelegate):
    """Numeric editor with range; float or int."""

    def __init__(self, minimum, maximum, decimals=2, step=0.1, parent=None):
        super().__init__(parent)
        self.minimum, self.maximum = minimum, maximum
        self.decimals, self.step = decimals, step

    def createEditor(self, parent, option, index):
        if self.decimals == 0:
            spn = QSpinBox(parent)
            spn.setRange(int(self.minimum), int(self.maximum))
        else:
            spn = QDoubleSpinBox(parent)
            spn.setRange(self.minimum, self.maximum)
            spn.setDecimals(self.decimals)
            spn.setSingleStep(self.step)
        return spn

    def setEditorData(self, editor, index):
        try:
            editor.setValue(type(editor.value())(index.data(Qt.ItemDataRole.EditRole)))
        except (TypeError, ValueError):
            _LOG.debug("Cell value %r not convertible; editor keeps its "
                       "default", index.data(Qt.ItemDataRole.EditRole))

    def setModelData(self, editor, model, index):
        editor.interpretText()
        model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)
