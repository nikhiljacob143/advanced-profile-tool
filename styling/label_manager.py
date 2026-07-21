# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Labelling for generated cross-section line layers.

Uses the ``label`` field (which already carries the formatted chainage,
e.g. "XS07 0+120.00") with HORIZONTAL placement. Cross-section lines are
short; QGIS parallel/curved "Line" placement silently drops any label
longer than the line, so at normal map scales section labels never
appeared. Horizontal placement anchors a label at each feature and
always renders it. The ``label_position`` setting (left / centre /
right) is honoured with a quadrant offset.

``label_position`` = "both" places a label at BOTH ends of each section
line. A layer can hold only one QgsVectorLayerSimpleLabeling, so this is
implemented with QgsRuleBasedLabeling carrying two rules (same features,
line anchor 0% and 100%).

Text appearance is read from settings: ``label_font_size`` (points,
default 8), ``label_font_family`` (empty = application default font) and
``label_buffer_mm`` (white halo size in millimetres).

When the ``label_major_only`` setting is truthy, a data-defined "Show"
property with the expression ``"is_major" = 1`` restricts labels to
major sections.

QGIS boundary module — imports qgis.core.
"""
import logging

_LOG = logging.getLogger(__name__)

from qgis.core import (Qgis, QgsMessageLog, QgsPalLayerSettings, QgsProperty,
                       QgsRuleBasedLabeling, QgsTextBufferSettings,
                       QgsTextFormat, QgsVectorLayerSimpleLabeling)
from qgis.PyQt.QtGui import QColor, QFont

from ..constants import DEFAULTS, LOG_TAG

# label_position setting → anchor percentage along the section line.
# Sections are built left point → centre → right point, so the line start
# is the LEFT end of the section.
_ANCHOR_PERCENT = {"left": 0.0, "centre": 0.5, "center": 0.5, "right": 1.0}


def _setting(settings, key, default=None):
    fallback = DEFAULTS.get(key, default)
    try:
        val = settings.get(key, fallback)
    except TypeError:
        val = settings.get(key)
    return fallback if val is None else val


def _horizontal_placement():
    """Horizontal placement enum across API variants.

    Cross-section lines are short (a few tens of metres); QGIS "Line"
    (parallel/curved) placement SILENTLY DROPS any label longer than the
    line it sits on, so at normal map scales no section label ever
    rendered. Horizontal placement anchors the label at each feature and
    always draws it — the correct choice for short section ticks."""
    try:
        return Qgis.LabelPlacement.Horizontal
    except AttributeError:
        return QgsPalLayerSettings.Placement.Horizontal


# label_position → quadrant offset applied to a horizontally-placed label.
# "left" puts the text off the left end, "right" off the right end,
# "centre" centres it over the section, "both" is handled with two rules.
_QUADRANT = {"left": (-1, 0), "centre": (0, 1), "center": (0, 1),
             "right": (1, 0)}


def _apply_position_offset(pal, position):
    """Nudge a horizontally-placed label towards the requested end of the
    section (left / centre / right) using a quadrant. Degrades silently if
    the quadrant enum is unavailable."""
    qx, qy = _QUADRANT.get(str(position).lower(), (0, 1))
    try:
        try:
            over = Qgis.LabelQuadrantPosition
        except AttributeError:
            over = QgsPalLayerSettings          # fallback holder
        # map (qx, qy) to a QuadrantPosition; centre-above by default
        lookup = {
            (-1, 0): "QuadrantLeft", (1, 0): "QuadrantRight",
            (0, 1): "QuadrantAbove"}
        name = lookup.get((qx, qy), "QuadrantAbove")
        quad = getattr(over, name, None)
        if quad is not None:
            pal.quadOffset = quad
    except Exception:                              # noqa: BLE001
        _LOG.debug("Quadrant offset not supported by this QGIS version; "
                   "labels stay centred", exc_info=True)


def _text_format(settings):
    """Text format from settings: font family (empty = default), size in
    points and a white buffer of ``label_buffer_mm`` millimetres."""
    fmt = QgsTextFormat()
    family = str(_setting(settings, "label_font_family", "") or "").strip()
    font = QFont(family) if family else QFont()
    font_size = float(_setting(settings, "label_font_size", 8))
    fmt.setFont(font)
    fmt.setSize(font_size)

    buffer_mm = float(_setting(settings, "label_buffer_mm", 0.8))
    buf = QgsTextBufferSettings()
    buf.setEnabled(buffer_mm > 0)
    buf.setSize(buffer_mm)         # millimetres
    buf.setColor(QColor(255, 255, 255))
    fmt.setBuffer(buf)
    return fmt


def _build_pal(settings, position):
    """One QgsPalLayerSettings for the given label position."""
    pal = QgsPalLayerSettings()
    pal.fieldName = "label"
    pal.isExpression = False
    pal.enabled = True
    # horizontal placement renders every label regardless of section
    # length (line placement dropped labels longer than the short
    # cross-section lines — the defect this fixes)
    pal.placement = _horizontal_placement()
    _apply_position_offset(pal, position)
    pal.setFormat(_text_format(settings))

    # Optionally restrict labelling to major sections via a data-defined
    # "Show" property.
    if _setting(settings, "label_major_only", False):
        show_prop = QgsPalLayerSettings.Property.Show
        props = pal.dataDefinedProperties()
        props.setProperty(show_prop,
                          QgsProperty.fromExpression('"is_major" = 1'))
        pal.setDataDefinedProperties(props)
    return pal


def apply_section_labels(layer, settings):
    """Apply labelling to a section line layer.

    Parameters
    ----------
    layer : QgsVectorLayer
        Line layer with the string field ``label`` (and integer field
        ``is_major`` when major-only labelling is requested).
    settings : SettingsManager or dict
        Source of label_position (left/centre/right/both),
        label_font_size, label_font_family, label_buffer_mm and
        label_major_only.

    Returns the QgsPalLayerSettings that were applied, or the
    QgsRuleBasedLabeling when label_position == "both" (two rules, one
    per line end).
    """
    position = str(_setting(settings, "label_position", "left")).lower()

    if position == "both":
        root = QgsRuleBasedLabeling.Rule(None)
        for pos, name in (("left", "Label — start"),
                          ("right", "Label — end")):
            rule = QgsRuleBasedLabeling.Rule(_build_pal(settings, pos))
            rule.setDescription(name)
            root.appendChild(rule)
        labeling = QgsRuleBasedLabeling(root)
        layer.setLabeling(labeling)
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()
        return labeling

    pal = _build_pal(settings, position)
    labeling = QgsVectorLayerSimpleLabeling(pal)
    layer.setLabeling(labeling)
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()
    return pal
