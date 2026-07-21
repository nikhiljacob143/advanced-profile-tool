# -*- coding: utf-8 -*-
"""Map tool: pick an alignment feature by clicking a line layer.

Copyright (C) 2026 Nikhil Jacob — GPL v2 or later.
"""
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.core import (QgsFeatureRequest, QgsGeometry, QgsPointXY,
                       QgsRectangle, QgsWkbTypes)


class AlignmentPickerTool(QgsMapTool):
    """Click near a feature of the target layer to select it.

    Emits feature_picked(feature_id:int)."""

    feature_picked = pyqtSignal(int)

    def __init__(self, canvas, layer):
        super().__init__(canvas)
        self.layer = layer
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._flash = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self._flash.setColor(Qt.GlobalColor.red)
        self._flash.setWidth(3)

    def canvasReleaseEvent(self, event):
        if self.layer is None:
            return
        pt = self.toLayerCoordinates(self.layer, event.pos())
        radius_map = self.canvas().mapUnitsPerPixel() * 8
        # radius in layer units via a small rect transform
        rect_map = QgsRectangle(pt.x() - radius_map, pt.y() - radius_map,
                                pt.x() + radius_map, pt.y() + radius_map)
        req = QgsFeatureRequest().setFilterRect(rect_map)
        best = None
        best_d = None
        probe = QgsGeometry.fromPointXY(QgsPointXY(pt))
        for f in self.layer.getFeatures(req):
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            d = g.distance(probe)
            if best is None or d < best_d:
                best, best_d = f, d
        if best is not None:
            self._flash.setToGeometry(best.geometry(), self.layer)
            self.feature_picked.emit(best.id())

    def deactivate(self):
        self._flash.reset(QgsWkbTypes.GeometryType.LineGeometry)
        super().deactivate()
