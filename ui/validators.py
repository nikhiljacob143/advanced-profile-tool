# -*- coding: utf-8 -*-
"""Input validators for chainage and numeric fields.

Copyright (C) 2026 Nikhil Jacob — GPL v2 or later.
"""
from qgis.PyQt.QtGui import QValidator

from ..core.units import parse_chainage


class ChainageValidator(QValidator):
    """Accepts '1+240.5', 'CH 1+240', 'STA 0+020', plain numbers, or
    empty text (treated as 'not set')."""

    def validate(self, text, pos):
        if not text.strip():
            return QValidator.State.Acceptable, text, pos
        if parse_chainage(text) is not None:
            return QValidator.State.Acceptable, text, pos
        # allow partial typing (digits, +, -, ., spaces, CH/STA prefixes)
        allowed = set("0123456789+-. CHSTAchsta")
        if all(ch in allowed for ch in text):
            return QValidator.State.Intermediate, text, pos
        return QValidator.State.Invalid, text, pos


def chainage_from_edit(line_edit, default=None):
    """Parsed chainage from a QLineEdit, or `default` when empty/invalid."""
    t = line_edit.text().strip()
    if not t:
        return default
    v = parse_chainage(t)
    return default if v is None else v
