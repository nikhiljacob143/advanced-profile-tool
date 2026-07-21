# -*- coding: utf-8 -*-
"""Canvas preview overlay for generated sections (rubber bands + ticks).

Copyright (C) 2026 Nikhil Jacob — GPL v2 or later.
"""
import logging

from qgis.PyQt.QtCore import Qt

_LOG = logging.getLogger(__name__)
from qgis.PyQt.QtGui import QColor
from qgis.gui import QgsRubberBand, QgsVertexMarker
from qgis.core import (QgsCoordinateTransform, QgsGeometry, QgsPointXY,
                       QgsProject, QgsWkbTypes,
                       QgsCoordinateReferenceSystem)

from ..core import geometry_math as gm

# Section labels on the preview are deliberately NOT drawn: QGIS rubber
# bands cannot carry text. The dock shows proper labels (label_manager)
# on the generated section layer after generation instead.


class SectionPreviewOverlay:
    """Draws section lines (minor/major), the alignment with direction
    arrows, and a hover position marker on the map canvas. All geometry
    supplied in the calculation CRS; transformed to the canvas CRS for
    display."""

    def __init__(self, canvas):
        self.canvas = canvas
        self._minor = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self._major = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self._align = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self._arrows = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self._marker = QgsVertexMarker(canvas)
        self._minor.setColor(QColor("#D32F2F"))
        self._minor.setWidth(1)
        self._major.setColor(QColor("#7B1FA2"))
        self._major.setWidth(2)
        self._align.setColor(QColor("#1565C0"))
        self._align.setWidth(2)
        self._align.setLineStyle(Qt.PenStyle.DashLine)
        self._arrows.setColor(QColor("#1565C0"))
        self._arrows.setWidth(2)
        self._marker.setColor(QColor("#F57C00"))
        self._marker.setIconType(QgsVertexMarker.IconType.ICON_CIRCLE)
        self._marker.setIconSize(12)
        self._marker.setPenWidth(2)
        self._marker.hide()
        self._calc_crs = None

    # ------------------------------------------------------------------ #
    def _tr(self):
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        if self._calc_crs and self._calc_crs.isValid() \
                and canvas_crs.isValid() and canvas_crs != self._calc_crs:
            return QgsCoordinateTransform(self._calc_crs, canvas_crs,
                                          QgsProject.instance())
        return None

    def set_colors(self, minor_hex, major_hex):
        self._minor.setColor(QColor(minor_hex))
        self._major.setColor(QColor(major_hex))

    def show_preview(self, alignment, sections, calc_crs_authid,
                     clear=True):
        """Render the alignment and section lines. clear=False adds this
        alignment's preview on top of the existing one (batch preview)."""
        self._calc_crs = QgsCoordinateReferenceSystem(calc_crs_authid)
        tr = self._tr()

        def conv(x, y):
            p = QgsPointXY(x, y)
            if tr:
                try:
                    p = tr.transform(p)
                except Exception:                      # noqa: BLE001
                    _LOG.debug("Preview point transform failed; drawing "
                               "untransformed point", exc_info=True)
            return p

        if clear:
            self.clear()
        apts = [conv(x, y) for x, y in alignment.vertices]
        self._align.setToGeometry(QgsGeometry.fromPolylineXY(apts), None)
        self._add_direction_arrows(alignment, conv)
        for sec in sections:
            lp, c, r = sec.left_point, sec.center, sec.right_point
            pts = [conv(*lp), conv(*c), conv(*r)]
            band = self._major if sec.is_major else self._minor
            band.addGeometry(QgsGeometry.fromPolylineXY(pts), None)
        self.canvas.refresh()

    def _add_direction_arrows(self, alignment, conv):
        """Small arrow ticks along the alignment showing the direction
        of increasing chainage: at every ~10% of length, two short barb
        segments angled 30° back from the local tangent."""
        verts, cum = alignment.vertices, alignment.cum_dist
        if len(verts) < 2 or not cum or cum[-1] <= 0:
            return
        L = cum[-1]
        size = min(max(L * 0.015, 0.5), 30.0)   # calc-CRS units
        for k in range(1, 10):                  # 10% .. 90%
            d = L * k / 10.0
            px, py = gm.point_at(verts, cum, d)
            tx, ty = gm.tangent_at(verts, cum, d)
            back = (-tx, -ty)
            for angle in (30.0, -30.0):
                bx, by = gm.rotate(back, angle)
                seg = [QgsPointXY(px, py),
                       QgsPointXY(px + bx * size, py + by * size)]
                seg = [conv(p.x(), p.y()) for p in seg]
                self._arrows.addGeometry(
                    QgsGeometry.fromPolylineXY(seg), None)

    def show_position(self, x, y):
        """Show the hover marker at a calc-CRS position."""
        tr = self._tr()
        p = QgsPointXY(x, y)
        if tr:
            try:
                p = tr.transform(p)
            except Exception:
                return
        self._marker.setCenter(p)
        self._marker.show()

    def hide_position(self):
        self._marker.hide()

    def clear(self):
        self._minor.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self._major.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self._align.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self._arrows.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self._marker.hide()

    def remove(self):
        self.clear()
        scene = self.canvas.scene()
        for item in (self._minor, self._major, self._align, self._arrows,
                     self._marker):
            try:
                scene.removeItem(item)
            except Exception:                          # noqa: BLE001
                _LOG.debug("Canvas item already removed", exc_info=True)
