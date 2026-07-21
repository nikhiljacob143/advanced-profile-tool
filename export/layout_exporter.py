# -*- coding: utf-8 -*-
"""QGIS Print Layout integration: builds a layout with a plan-view map
frame, a profile image frame and a title block, saved into the project.

Copyright (C) 2026 Nikhil Jacob — GPL v2 or later.
"""
from qgis.core import (QgsLayoutExporter, QgsLayoutItemLabel,
                       QgsLayoutItemMap, QgsLayoutItemPage,
                       QgsLayoutItemPicture, QgsLayoutPoint, QgsLayoutSize,
                       QgsPrintLayout, QgsProject, QgsRectangle,
                       QgsUnitTypes)

PAGE_SIZES = {"A4": (297.0, 210.0), "A3": (420.0, 297.0),
              "A1": (841.0, 594.0)}


def build_profile_layout(name, extent_rect, profile_image_path,
                         title_block=None, page="A3", logo_path=None):
    """Create (or replace) a print layout in the current project.

    extent_rect: QgsRectangle (map CRS) around the alignment for the map
    frame. profile_image_path: rendered long-section PNG. title_block: dict
    (project, client, site, alignment, date, author, reviewer,
    drawing_number, revision). Returns the QgsPrintLayout.
    """
    proj = QgsProject.instance()
    manager = proj.layoutManager()
    existing = manager.layoutByName(name)
    if existing is not None:
        manager.removeLayout(existing)
    layout = QgsPrintLayout(proj)
    layout.initializeDefaults()
    layout.setName(name)
    w, h = PAGE_SIZES.get(page, PAGE_SIZES["A3"])
    pc = layout.pageCollection()
    pc.page(0).setPageSize(QgsLayoutSize(w, h,
                                         QgsUnitTypes.LayoutUnit.LayoutMillimeters))
    margin = 10.0
    tb_h = 28.0
    # ---- map frame (upper half) ---------------------------------------
    map_item = QgsLayoutItemMap(layout)
    map_item.setRect(0, 0, 10, 10)  # placeholder rect required before move
    map_h = (h - 2 * margin - tb_h) * 0.45
    map_item.attemptMove(QgsLayoutPoint(margin, margin,
                                        QgsUnitTypes.LayoutUnit.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(w - 2 * margin, map_h,
                                         QgsUnitTypes.LayoutUnit.LayoutMillimeters))
    if extent_rect is not None and not extent_rect.isEmpty():
        ext = QgsRectangle(extent_rect)
        ext.scale(1.2)
        map_item.setExtent(ext)
    map_item.setFrameEnabled(True)
    layout.addLayoutItem(map_item)
    # ---- profile image (lower half) ------------------------------------
    if profile_image_path:
        pic = QgsLayoutItemPicture(layout)
        pic.setPicturePath(profile_image_path)
        pic.attemptMove(QgsLayoutPoint(
            margin, margin + map_h + 4.0, QgsUnitTypes.LayoutUnit.LayoutMillimeters))
        pic.attemptResize(QgsLayoutSize(
            w - 2 * margin, h - 2 * margin - tb_h - map_h - 8.0,
            QgsUnitTypes.LayoutUnit.LayoutMillimeters))
        pic.setResizeMode(QgsLayoutItemPicture.ResizeMode.Zoom)
        pic.setFrameEnabled(True)
        layout.addLayoutItem(pic)
    # ---- title block strip ----------------------------------------------
    tb = dict(title_block or {})
    y0 = h - margin - tb_h
    x = margin
    if logo_path:
        logo = QgsLayoutItemPicture(layout)
        logo.setPicturePath(logo_path)
        logo.attemptMove(QgsLayoutPoint(x, y0 + 2,
                                        QgsUnitTypes.LayoutUnit.LayoutMillimeters))
        logo.attemptResize(QgsLayoutSize(30, tb_h - 4,
                                         QgsUnitTypes.LayoutUnit.LayoutMillimeters))
        logo.setResizeMode(QgsLayoutItemPicture.ResizeMode.Zoom)
        layout.addLayoutItem(logo)
        x += 34
    fields = [("Project", tb.get("project", "")),
              ("Client", tb.get("client", "")),
              ("Site", tb.get("site", "")),
              ("Alignment", tb.get("alignment", "")),
              ("Date", tb.get("date", "")),
              ("Author", tb.get("author", "")),
              ("Reviewer", tb.get("reviewer", "")),
              ("Drawing no.", tb.get("drawing_number", "")),
              ("Rev", tb.get("revision", ""))]
    cell_w = (w - margin - x) / len(fields)
    for i, (label, value) in enumerate(fields):
        item = QgsLayoutItemLabel(layout)
        item.setText(f"{label}\n{value}")
        item.setMargin(1.0)
        item.attemptMove(QgsLayoutPoint(
            x + i * cell_w, y0, QgsUnitTypes.LayoutUnit.LayoutMillimeters))
        item.attemptResize(QgsLayoutSize(
            cell_w, tb_h, QgsUnitTypes.LayoutUnit.LayoutMillimeters))
        item.setFrameEnabled(True)
        layout.addLayoutItem(item)
    manager.addLayout(layout)
    return layout


def export_layout_pdf(layout, path):
    """Export a layout to PDF. Returns (ok, error_message)."""
    exporter = QgsLayoutExporter(layout)
    res = exporter.exportToPdf(
        path, QgsLayoutExporter.PdfExportSettings())
    if res == QgsLayoutExporter.ExportResult.Success:
        return True, ""
    return False, f"Layout PDF export failed (code {res})."
