# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Rule-based styling for generated cross-section line layers.

Applies a two-rule QgsRuleBasedRenderer keyed on the integer field
``is_major``: major sections (is_major = 1) are drawn with the major
colour/width from the plugin settings, all other features (is_major = 0
or NULL) with the standard colour/width. Line style (solid/dash/dot/
dash dot) comes from ``line_style``; the major rule may carry its own
dash style via ``line_style_major`` (falls back to ``line_style`` when
empty).

QGIS boundary module — imports qgis.core.
"""
from qgis.core import QgsLineSymbol, QgsRuleBasedRenderer

from ..constants import DEFAULTS

# Pen style names accepted by QgsLineSymbol.createSimple()'s "line_style"
# property, keyed by the plugin's line_style setting values.
PEN_STYLES = {
    "solid": "solid",
    "dash": "dash",
    "dot": "dot",
    "dash dot": "dash dot",
}


def _setting(settings, key):
    """Read a key from a SettingsManager or plain dict with the factory
    default as fallback."""
    try:
        return settings.get(key, DEFAULTS.get(key))
    except TypeError:
        # settings.get implementations that only accept one argument
        val = settings.get(key)
        return DEFAULTS.get(key) if val is None else val


def _line_symbol(color, width_mm, pen_style):
    """Build a simple line symbol with millimetre width."""
    return QgsLineSymbol.createSimple({
        "line_color": str(color),
        "line_width": str(float(width_mm)),
        "line_width_unit": "MM",
        "line_style": pen_style,
        "capstyle": "round",
        "joinstyle": "round",
    })


def apply_section_style(layer, settings):
    """Apply the major/minor rule-based renderer to a section line layer.

    Parameters
    ----------
    layer : QgsVectorLayer
        Line layer holding the generated sections. Must contain the
        integer field ``is_major`` (1 = major section).
    settings : SettingsManager or dict
        Source of line_color, line_color_major, line_width_mm,
        line_width_major_mm, line_style and line_style_major (major-rule
        dash style; empty/None falls back to line_style).

    Returns the renderer that was applied.
    """
    pen_style = PEN_STYLES.get(str(_setting(settings, "line_style")).lower(),
                               "solid")
    major_style_raw = _setting(settings, "line_style_major") \
        or _setting(settings, "line_style")
    pen_style_major = PEN_STYLES.get(str(major_style_raw).lower(),
                                     pen_style)

    major_symbol = _line_symbol(_setting(settings, "line_color_major"),
                                _setting(settings, "line_width_major_mm"),
                                pen_style_major)
    minor_symbol = _line_symbol(_setting(settings, "line_color"),
                                _setting(settings, "line_width_mm"),
                                pen_style)

    root = QgsRuleBasedRenderer.Rule(None)

    major_rule = QgsRuleBasedRenderer.Rule(major_symbol)
    major_rule.setFilterExpression('"is_major" = 1')
    major_rule.setLabel("Major section")
    root.appendChild(major_rule)

    minor_rule = QgsRuleBasedRenderer.Rule(minor_symbol)
    minor_rule.setFilterExpression('"is_major" = 0 OR "is_major" IS NULL')
    minor_rule.setLabel("Section")
    root.appendChild(minor_rule)

    renderer = QgsRuleBasedRenderer(root)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return renderer
