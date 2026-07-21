# -*- coding: utf-8 -*-
"""Map tool: click on/near the alignment to add a manual section chainage.

Copyright (C) 2026 Nikhil Jacob — GPL v2 or later.
"""
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.gui import QgsMapTool
from qgis.core import (QgsCoordinateTransform, QgsGeometry, QgsPointXY,
                       QgsProject)


class SectionAddTool(QgsMapTool):
    """Click near the resolved alignment polyline; the clicked position is
    projected onto the alignment and the displayed chainage is emitted as
    chainage_picked(float)."""

    chainage_picked = pyqtSignal(float)

    def __init__(self, canvas, alignment, calc_crs):
        super().__init__(canvas)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.alignment = alignment
        self.calc_crs = calc_crs
        pts = [QgsPointXY(x, y) for x, y in alignment.vertices]
        self._geom = QgsGeometry.fromPolylineXY(pts)

    def canvasReleaseEvent(self, event):
        p_canvas = self.toMapCoordinates(event.pos())
        # canvas CRS → calculation CRS
        canvas_crs = self.canvas().mapSettings().destinationCrs()
        pt = QgsPointXY(p_canvas)
        if canvas_crs.isValid() and self.calc_crs.isValid() \
                and canvas_crs != self.calc_crs:
            tr = QgsCoordinateTransform(canvas_crs, self.calc_crs,
                                        QgsProject.instance())
            try:
                pt = tr.transform(pt)
            except Exception:
                return
        d = self._geom.lineLocatePoint(QgsGeometry.fromPointXY(pt))
        if d < 0:
            return
        chainage = d + self.alignment.start_chainage
        self.chainage_picked.emit(float(chainage))
