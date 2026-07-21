# -*- coding: utf-8 -*-
"""Qt table model for the DEM surfaces list.

Copyright (C) 2026 Nikhil Jacob — GPL v2 or later.
"""
from qgis.PyQt.QtCore import QAbstractTableModel, QModelIndex, Qt
from qgis.PyQt.QtGui import QBrush, QColor

from ..constants import INTERP_METHODS

COLUMNS = ["On", "Ref", "Name", "Band", "Interp", "V offset",
           "Colour", "Style", "Width", "Units", "Datum note"]
COL_ON, COL_REF, COL_NAME, COL_BAND, COL_INTERP, COL_VOFF, COL_COLOR, \
    COL_STYLE, COL_WIDTH, COL_UNITS, COL_DATUM = range(11)

LINE_STYLES = ["solid", "dash", "dot", "dash dot"]


class DemTableModel(QAbstractTableModel):
    """Editable model over a list[DemDef]."""

    def __init__(self, dem_defs=None, parent=None):
        super().__init__(parent)
        self.dems = dem_defs if dem_defs is not None else []

    # ---- structure ---------------------------------------------------- #
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.dems)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, i, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[i]
        return None

    def flags(self, index):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        c = index.column()
        if c in (COL_ON, COL_REF):
            f |= Qt.ItemFlag.ItemIsUserCheckable
        elif c != COL_NAME or True:
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    # ---- data ---------------------------------------------------------- #
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        d = self.dems[index.row()]
        c = index.column()
        if role == Qt.ItemDataRole.CheckStateRole:
            if c == COL_ON:
                return Qt.CheckState.Checked if d.enabled else Qt.CheckState.Unchecked
            if c == COL_REF:
                return Qt.CheckState.Checked if d.is_reference else Qt.CheckState.Unchecked
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if c == COL_NAME:
                return d.name
            if c == COL_BAND:
                return d.band
            if c == COL_INTERP:
                return d.interp
            if c == COL_VOFF:
                return f"{d.v_offset:g}" if role == Qt.ItemDataRole.DisplayRole \
                    else d.v_offset
            if c == COL_COLOR:
                return d.color
            if c == COL_STYLE:
                return d.line_style
            if c == COL_WIDTH:
                return f"{d.line_width:g}" if role == Qt.ItemDataRole.DisplayRole \
                    else d.line_width
            if c == COL_UNITS:
                return d.v_units
            if c == COL_DATUM:
                return d.datum_note
        if role == Qt.ItemDataRole.BackgroundRole and c == COL_COLOR:
            return QBrush(QColor(d.color))
        if role == Qt.ItemDataRole.ToolTipRole:
            return (f"{d.name}\npixel ≈ {d.pixel_size:g} m"
                    if d.pixel_size else d.name)
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False
        d = self.dems[index.row()]
        c = index.column()
        if role == Qt.ItemDataRole.CheckStateRole:
            state = value == Qt.CheckState.Checked
            if c == COL_ON:
                d.enabled = state
            elif c == COL_REF:
                if state:
                    for other in self.dems:
                        other.is_reference = False
                d.is_reference = state
                self.dataChanged.emit(
                    self.index(0, COL_REF),
                    self.index(len(self.dems) - 1, COL_REF))
            self.dataChanged.emit(index, index)
            return True
        if role == Qt.ItemDataRole.EditRole:
            try:
                if c == COL_NAME:
                    d.name = str(value).strip() or d.name
                elif c == COL_BAND:
                    d.band = max(1, int(value))
                elif c == COL_INTERP:
                    v = str(value).strip().lower()
                    if v in INTERP_METHODS:
                        d.interp = v
                elif c == COL_VOFF:
                    d.v_offset = float(value)
                elif c == COL_COLOR:
                    v = str(value).strip()
                    if v.startswith("#") and len(v) in (7, 9):
                        d.color = v
                elif c == COL_STYLE:
                    v = str(value).strip().lower()
                    if v in LINE_STYLES:
                        d.line_style = v
                elif c == COL_WIDTH:
                    d.line_width = min(max(float(value), 0.1), 10.0)
                elif c == COL_UNITS:
                    d.v_units = str(value).strip() or "m"
                elif c == COL_DATUM:
                    d.datum_note = str(value)
                else:
                    return False
            except (TypeError, ValueError):
                return False
            self.dataChanged.emit(index, index)
            return True
        return False

    # ---- operations ---------------------------------------------------- #
    def add(self, dem_def):
        self.beginInsertRows(QModelIndex(), len(self.dems), len(self.dems))
        if not any(d.is_reference for d in self.dems):
            dem_def.is_reference = True
        self.dems.append(dem_def)
        self.endInsertRows()

    def remove(self, row):
        if 0 <= row < len(self.dems):
            self.beginRemoveRows(QModelIndex(), row, row)
            self.dems.pop(row)
            self.endRemoveRows()

    def move(self, row, delta):
        new = row + delta
        if 0 <= row < len(self.dems) and 0 <= new < len(self.dems):
            self.beginResetModel()
            self.dems.insert(new, self.dems.pop(row))
            self.endResetModel()

    def enabled_dems(self):
        return [d for d in self.dems if d.enabled]
