# -*- coding: utf-8 -*-
"""Run-level validation: DEM coverage, unit/datum consistency, parameter
sanity. QGIS boundary (layer access), logic kept simple and reportable."""
import logging

from qgis.core import QgsCoordinateTransform, QgsProject

_LOG = logging.getLogger(__name__)


def check_dem_coverage(sections, dem_defs, layer_map, calc_crs):
    """Warn for sections that fall completely outside every enabled DEM.

    Returns (warnings:list[str], outside_ids:set[int]).
    """
    warnings = []
    extents = []
    for dd in dem_defs:
        if not dd.enabled:
            continue
        lyr = layer_map.get(dd.layer_id)
        if lyr is None:
            continue
        ext = lyr.extent()
        if lyr.crs().isValid() and calc_crs.isValid() \
                and lyr.crs() != calc_crs:
            try:
                tr = QgsCoordinateTransform(lyr.crs(), calc_crs,
                                            QgsProject.instance())
                ext = tr.transformBoundingBox(ext)
            except Exception:                          # noqa: BLE001
                _LOG.debug("DEM extent transform failed; layer skipped in "
                           "coverage check", exc_info=True)
                continue
        extents.append(ext)
    outside = set()
    if not extents:
        return ["No enabled DEM layers to check coverage against."], outside
    for sec in sections:
        pts = [sec.left_point, sec.center, sec.right_point]
        covered = any(
            e.xMinimum() <= x <= e.xMaximum() and
            e.yMinimum() <= y <= e.yMaximum()
            for e in extents for (x, y) in pts)
        if not covered:
            outside.add(sec.section_id)
    if outside:
        warnings.append(
            f"{len(outside)} section(s) lie completely outside all enabled "
            "DEM extents and will return no elevations.")
    return warnings, outside


def check_dem_consistency(dem_defs):
    """Warn when DEMs appear to use different vertical units or datums."""
    warnings = []
    enabled = [d for d in dem_defs if d.enabled]
    units = {d.v_units or "m" for d in enabled}
    if len(units) > 1:
        warnings.append(
            f"DEMs use mixed vertical units ({', '.join(sorted(units))}). "
            "Apply vertical offsets or convert before comparing surfaces.")
    datums = {d.datum_note.strip() for d in enabled if d.datum_note.strip()}
    if len(datums) > 1:
        warnings.append(
            "DEM datum notes differ: " + "; ".join(sorted(datums)) +
            ". Confirm surfaces share a vertical datum before volume "
            "calculations.")
    return warnings


def check_parameters(settings, alignment=None):
    """Sanity-check numeric parameters. Returns list of error strings
    (empty = OK)."""
    errors = []
    if settings.get("left_width", 0) <= 0 and \
            settings.get("right_width", 0) <= 0:
        errors.append("Section width must be greater than zero on at least "
                      "one side.")
    si = settings.get("sampling_interval", 0)
    if si < 0:
        errors.append("Sampling interval cannot be negative.")
    if alignment is not None and si > 0 and si > alignment.length:
        errors.append("Sampling interval exceeds the alignment length.")
    return errors
