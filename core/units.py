# -*- coding: utf-8 -*-
"""Chainage formatting/parsing and unit helpers. Pure Python — no qgis imports."""
import re

from ..constants import (CHAINAGE_PLAIN, CHAINAGE_STATION, CHAINAGE_CH,
                         CHAINAGE_STA)

_PREFIXES = {CHAINAGE_STATION: "", CHAINAGE_CH: "CH ", CHAINAGE_STA: "STA "}


def format_chainage(value, fmt=CHAINAGE_STATION, decimals=2):
    """Format a chainage in metres to the requested convention.

    >>> format_chainage(1240.5)
    '1+240.50'
    >>> format_chainage(-15.0, CHAINAGE_CH, 0)
    'CH -0+015'
    """
    if value is None:
        return ""
    if fmt == CHAINAGE_PLAIN:
        return f"{value:.{decimals}f}"
    prefix = _PREFIXES.get(fmt, "")
    sign = "-" if value < 0 else ""
    v = abs(float(value))
    km = int(v // 1000)
    rem = v - km * 1000
    # guard against 999.9995 rounding to 1000.000
    rem_str = f"{rem:0{4 + decimals if decimals else 3}.{decimals}f}"
    if float(rem_str) >= 1000.0:
        km += 1
        rem_str = f"{0:0{4 + decimals if decimals else 3}.{decimals}f}"
    return f"{prefix}{sign}{km}+{rem_str}"


_CHAINAGE_RE = re.compile(
    r"^\s*(?:CH|STA)?\s*(-?)\s*(?:(\d+)\s*\+\s*)?(\d+(?:\.\d*)?)\s*$",
    re.IGNORECASE)


def parse_chainage(text):
    """Parse '1+240.5', 'CH 1+240', 'STA 0+020', '1240.5' → metres (float).

    Returns None when the text is not a valid chainage.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = _CHAINAGE_RE.match(str(text))
    if not m:
        return None
    sign = -1.0 if m.group(1) == "-" else 1.0
    km = float(m.group(2)) if m.group(2) else 0.0
    rem = float(m.group(3))
    if m.group(2) is not None and rem >= 1000.0:
        return None
    return sign * (km * 1000.0 + rem)


def format_offset(value, convention="neg_left", decimals=2):
    """Format a signed offset (negative = left internally) for display."""
    if convention == "pos_both":
        side = "L" if value < 0 else "R" if value > 0 else ""
        return f"{abs(value):.{decimals}f}{side}"
    return f"{value:.{decimals}f}"


M_PER_FT = 0.3048


def to_display_units(value_m, units="m"):
    return value_m / M_PER_FT if units == "ft" else value_m


def from_display_units(value, units="m"):
    return value * M_PER_FT if units == "ft" else value
