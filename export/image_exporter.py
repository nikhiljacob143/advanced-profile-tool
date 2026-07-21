# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Image export helpers for matplotlib figures.

Forces the Agg (non-interactive raster) backend so figure rendering works
inside QGIS worker threads and in headless test environments alike.
No qgis imports.
"""
import logging
import re

logger = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")
except ImportError:                      # pragma: no cover - env dependent
    matplotlib = None
except Exception as exc:                 # backend already fixed elsewhere
    logger.debug("Could not switch matplotlib backend to Agg: %s", exc)

__all__ = ["save_figure", "build_image_name"]


def save_figure(fig, path, dpi=300, transparent=False):
    """Save a matplotlib figure to ``path`` (PNG or any supported format)
    and return the path written.

    ``dpi`` defaults to 300 for report-quality raster output;
    ``transparent=True`` renders with a transparent background (useful for
    overlaying on title sheets).
    """
    fig.savefig(str(path), dpi=dpi, transparent=transparent,
                bbox_inches="tight", facecolor=fig.get_facecolor())
    return str(path)


def build_image_name(section_label, ext="png"):
    """Build a filesystem-safe image filename from a section label.

    >>> build_image_name("XS01 0+020.00")
    'XS01_0+020.00.png'
    """
    safe = re.sub(r'[<>:"/\\|?*\s]+', "_", str(section_label)).strip("_.")
    if not safe:
        safe = "section"
    ext = str(ext).lstrip(".")
    return f"{safe}.{ext}"
