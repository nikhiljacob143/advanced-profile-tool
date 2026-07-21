# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Pure styling helpers for the matplotlib profile plot.

No qgis imports — safe for headless testing. Maps plugin/DemDef style
settings onto matplotlib keyword arguments and provides light/dark theme
colours consistent with the QGIS interface themes.
"""

# Plugin line style names → matplotlib linestyle codes.
LINESTYLE_MAP = {
    "solid": "-",
    "dash": "--",
    "dot": ":",
    "dash dot": "-.",
}

# Theme palettes (hex values suitable for matplotlib).
_DARK_THEME = {
    "background": "#2b2b2b",
    "foreground": "#eeeeee",
    "grid": "#555555",
}
_LIGHT_THEME = {
    "background": "#ffffff",
    "foreground": "#222222",
    "grid": "#cccccc",
}


def _attr(obj, name, default):
    """Read `name` from a dataclass attribute or a mapping key."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def dem_style(dem_def):
    """Matplotlib style kwargs for one DEM surface.

    Parameters
    ----------
    dem_def : DemDef or dict
        Provides color, line_style and line_width.

    Returns
    -------
    dict with keys ``color``, ``linestyle`` and ``linewidth`` ready to be
    passed to ``Axes.plot``.
    """
    style = str(_attr(dem_def, "line_style", "solid")).lower()
    return {
        "color": _attr(dem_def, "color", "#1976D2"),
        "linestyle": LINESTYLE_MAP.get(style, "-"),
        "linewidth": float(_attr(dem_def, "line_width", 1.2)),
    }


def qgis_theme_colors(dark: bool) -> dict:
    """Plot colours matching the active QGIS interface theme.

    Returns a dict with ``background``, ``foreground`` and ``grid`` hex
    colours for the requested theme.
    """
    return dict(_DARK_THEME if dark else _LIGHT_THEME)
