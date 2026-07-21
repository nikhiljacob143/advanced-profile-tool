# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""DXF exporters for cross-section geometry and gridded section sheets.

Two output modes:

* :func:`export_geometry_dxf` — bare profile polylines, either in
  section-local coordinates (x = offset, y = elevation, sections tiled
  vertically) or in world plan coordinates when ``world=True``.
* :func:`export_sheet_dxf` — Civil-3D-style gridded sheets with an axes
  box, elevation/offset grid lines, grid value labels, a labelled datum
  line and section titles, tiled in a grid layout.

Offsets follow the plugin convention: negative = left of the alignment,
positive = right. NaN elevations (NoData) split the profile polylines
into separate runs. No qgis imports.
"""
import logging
import math
import re

import numpy as np

from ..constants import DEFAULTS, DXF_LAYER_PREFIX
from ..core.units import format_chainage
from .dxf_backend import DxfDoc, hex_to_aci

logger = logging.getLogger(__name__)

__all__ = ["export_geometry_dxf", "export_sheet_dxf", "hex_to_aci"]

# fixed sheet layers
LAYER_GRID = DXF_LAYER_PREFIX + "GRID"
LAYER_TEXT = DXF_LAYER_PREFIX + "TEXT"
LAYER_DATUM = DXF_LAYER_PREFIX + "DATUM"


def _setting(settings, key, fallback=None):
    """Read ``key`` from a settings mapping/manager with factory fallback."""
    default = DEFAULTS.get(key, fallback)
    if settings is None:
        return default
    try:
        value = settings.get(key, default)
    except (TypeError, AttributeError):
        return default
    return default if value is None else value


def sanitise_layer_name(name):
    """Return a DXF-safe layer name (alphanumerics, underscore, hyphen)."""
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(name)).strip("_")
    return cleaned or "LAYER"


def _dem_layer_map(dem_defs):
    """Map dem.layer_id → (dxf layer name, colour index) for enabled DEMs.

    Duplicate sanitised names receive a numeric suffix so every DEM keeps
    its own layer.
    """
    mapping = {}
    used = set()
    for dem in dem_defs:
        if not getattr(dem, "enabled", True):
            continue
        base = DXF_LAYER_PREFIX + sanitise_layer_name(dem.name)
        name = base
        n = 1
        while name.upper() in used:
            name = f"{base}_{n}"
            n += 1
        used.add(name.upper())
        mapping[dem.layer_id] = (name, hex_to_aci(dem.color))
    return mapping


def _valid_runs(xs, ys):
    """Split paired arrays into runs of consecutive finite points.

    Yields lists of (x, y) tuples with at least two points each; NaN in
    either array terminates the current run (NoData gap handling).
    """
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    ok = np.isfinite(xs) & np.isfinite(ys)
    run = []
    for i in range(len(ok)):
        if ok[i]:
            run.append((float(xs[i]), float(ys[i])))
        else:
            if len(run) >= 2:
                yield run
            run = []
    if len(run) >= 2:
        yield run


def _finite_range(values):
    """Return (min, max) over finite entries, or None when none exist."""
    arr = np.asarray(values, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return None
    return float(finite.min()), float(finite.max())


def _section_elev_range(result, layer_map):
    """Finite elevation range across the mapped DEM lines of one section."""
    lo, hi = math.inf, -math.inf
    for layer_id in layer_map:
        line = result.lines.get(layer_id)
        if line is None or line.elevations is None:
            continue
        rng = _finite_range(line.elevations)
        if rng is not None:
            lo = min(lo, rng[0])
            hi = max(hi, rng[1])
    if lo is math.inf:
        return None
    return lo, hi


def export_geometry_dxf(path, profile_results, dem_defs, settings,
                        world=False):
    """Write bare profile polylines to DXF and return the path.

    Default (section-local) mode: each section is drawn with x = signed
    offset and y = elevation reduced to a common datum (the minimum
    elevation over all sections), then tiled vertically. The vertical
    spacing between tiles is the maximum section elevation range plus the
    ``dxf_tile_gap`` setting (default 50 drawing units), so tiles cannot
    overlap. When ``world=True`` plan polylines are drawn using each
    sample's world x/y instead.

    One layer per enabled DEM, named ``APT_<dem name sanitised>`` and
    coloured with the nearest basic AutoCAD colour index to ``dem.color``.
    NaN elevation runs split the polylines. Raises ValueError on empty
    inputs.
    """
    profile_results = list(profile_results or [])
    if not profile_results:
        raise ValueError("No profile results to export — nothing to draw.")
    layer_map = _dem_layer_map(dem_defs or [])
    if not layer_map:
        raise ValueError("No enabled DEM surfaces — nothing to draw.")

    tile_gap = float(_setting(settings, "dxf_tile_gap", 50.0))

    doc = DxfDoc(str(path))
    for name, aci in layer_map.values():
        doc.add_layer(name, aci)

    if world:
        for result in profile_results:
            for layer_id, (layer_name, _aci) in layer_map.items():
                line = result.lines.get(layer_id)
                if line is None or line.elevations is None:
                    continue
                elev = np.asarray(line.elevations, dtype=float)
                xs = np.asarray(result.xs, dtype=float).copy()
                # mask NoData samples so plan lines split at gaps too
                xs[~np.isfinite(elev)] = np.nan
                for run in _valid_runs(xs, result.ys):
                    doc.polyline(run, layer=layer_name)
        doc.save()
        return str(path)

    # section-local mode — establish a common datum and tile spacing
    global_lo, global_hi = math.inf, -math.inf
    max_range = 0.0
    for result in profile_results:
        rng = _section_elev_range(result, layer_map)
        if rng is None:
            continue
        global_lo = min(global_lo, rng[0])
        global_hi = max(global_hi, rng[1])
        max_range = max(max_range, rng[1] - rng[0])
    if global_lo is math.inf:
        raise ValueError("All profile samples are NoData — nothing to draw.")
    step = math.ceil(max_range) + tile_gap

    for index, result in enumerate(profile_results):
        y_base = index * step
        for layer_id, (layer_name, _aci) in layer_map.items():
            line = result.lines.get(layer_id)
            if line is None or line.elevations is None:
                continue
            elev = np.asarray(line.elevations, dtype=float)
            ys = (elev - global_lo) + y_base
            for run in _valid_runs(line.offsets, ys):
                doc.polyline(run, layer=layer_name)
    doc.save()
    return str(path)


def _grid_interval(elev_range):
    """Pick an elevation grid interval (1/2/5/10) for a tidy line count."""
    for interval in (1.0, 2.0, 5.0):
        if elev_range / interval <= 12.0:
            return interval
    return 10.0


def _ticks(start, stop, interval):
    """Multiples of ``interval`` in the open interval (start, stop)."""
    first = math.floor(start / interval) * interval + interval
    values = []
    v = first
    while v < stop - 1e-9:
        if v > start + 1e-9:
            values.append(round(v, 6))
        v += interval
    return values


def export_sheet_dxf(path, profile_results, dem_defs, settings,
                     comparisons=None):
    """Write Civil-3D-style gridded section sheets to DXF; return the path.

    For each section an axes box is drawn sized to the data extents
    rounded outward to 5 m, with elevation grid lines (interval selected
    automatically from 1/2/5/10 m), offset grid lines every 5 m (10 m for
    sections wider than 60 m), value labels on the grid, a datum line
    labelled ``DATUM RL <value>`` and a title (label plus formatted
    chainage). Profile polylines are drawn per enabled DEM on their own
    layers, split at NaN gaps.

    The ``dxf_vertical_exaggeration`` setting (default 1.0) scales
    elevations; when it is not 1 a ``(VE nx)`` note is appended to each
    title. Sections are tiled in a grid of ``dxf_sheet_cols`` columns
    (default 4), with spacing derived from the largest section extents
    plus margins. Optional ``comparisons`` (SectionComparison list) add a
    cut/fill area annotation to matching sections. Raises ValueError on
    empty inputs.
    """
    profile_results = list(profile_results or [])
    if not profile_results:
        raise ValueError("No profile results to export — nothing to draw.")
    layer_map = _dem_layer_map(dem_defs or [])
    if not layer_map:
        raise ValueError("No enabled DEM surfaces — nothing to draw.")

    ve = float(_setting(settings, "dxf_vertical_exaggeration", 1.0))
    text_h = float(_setting(settings, "dxf_text_height", 0.5))
    cols = max(1, int(_setting(settings, "dxf_sheet_cols", 4)))
    ch_fmt = _setting(settings, "chainage_format")
    ch_dec = int(_setting(settings, "decimals_chainage", 2))

    comp_by_id = {}
    for c in (comparisons or []):
        comp_by_id[c.section_id] = c

    # pre-compute per-section frames (extents rounded outward to 5 m)
    frames = []
    for result in profile_results:
        off_rng = _finite_range(result.offsets)
        elev_rng = _section_elev_range(result, layer_map)
        if off_rng is None or elev_rng is None:
            logger.warning("Section '%s' has no finite samples — skipped.",
                           result.label)
            continue
        off_lo = math.floor(off_rng[0] / 5.0) * 5.0
        off_hi = math.ceil(off_rng[1] / 5.0) * 5.0
        if off_hi <= off_lo:
            off_hi = off_lo + 5.0
        datum = math.floor(elev_rng[0] / 5.0) * 5.0
        top = math.ceil(elev_rng[1] / 5.0) * 5.0
        if top <= datum:
            top = datum + 5.0
        frames.append((result, off_lo, off_hi, datum, top))
    if not frames:
        raise ValueError("All sections are NoData — nothing to draw.")

    max_w = max(hi - lo for _r, lo, hi, _d, _t in frames)
    max_h = max((top - datum) * ve for _r, _lo, _hi, datum, top in frames)
    margin = max(20.0, 10.0 * text_h + 10.0)
    cell_w = max_w + margin
    cell_h = max_h + margin

    doc = DxfDoc(str(path))
    doc.add_layer(LAYER_GRID, 8)
    doc.add_layer(LAYER_TEXT, 7)
    doc.add_layer(LAYER_DATUM, 4)
    for name, aci in layer_map.values():
        doc.add_layer(name, aci)

    for index, (result, off_lo, off_hi, datum, top) in enumerate(frames):
        ox = (index % cols) * cell_w
        oy = -(index // cols) * cell_h

        def tx(off, _ox=ox, _lo=off_lo):
            return _ox + (off - _lo)

        def ty(elev, _oy=oy, _datum=datum):
            return _oy + (elev - _datum) * ve

        # axes box
        doc.polyline([(tx(off_lo), ty(datum)), (tx(off_hi), ty(datum)),
                      (tx(off_hi), ty(top)), (tx(off_lo), ty(top))],
                     layer=LAYER_GRID, closed=True)

        # elevation grid lines and RL labels
        e_int = _grid_interval(top - datum)
        for level in [datum] + _ticks(datum, top, e_int) + [top]:
            if datum < level < top:
                doc.line((tx(off_lo), ty(level)), (tx(off_hi), ty(level)),
                         layer=LAYER_GRID)
            doc.text(f"{level:g}", (tx(off_lo) - text_h, ty(level)),
                     height=text_h, layer=LAYER_TEXT, halign="RIGHT")

        # offset grid lines and labels (negative = left, positive = right)
        o_int = 5.0 if (off_hi - off_lo) <= 60.0 else 10.0
        for off in [off_lo] + _ticks(off_lo, off_hi, o_int) + [off_hi]:
            if off_lo < off < off_hi:
                doc.line((tx(off), ty(datum)), (tx(off), ty(top)),
                         layer=LAYER_GRID)
            doc.text(f"{off:g}", (tx(off), ty(datum) - 2.0 * text_h),
                     height=text_h, layer=LAYER_TEXT, halign="CENTER")

        # datum line with label
        doc.line((tx(off_lo), ty(datum)), (tx(off_hi), ty(datum)),
                 layer=LAYER_DATUM)
        doc.text(f"DATUM RL {datum:g}",
                 (tx(off_lo), ty(datum) - 4.0 * text_h),
                 height=text_h, layer=LAYER_TEXT)

        # title (label + chainage, VE note when applicable)
        title = f"{result.label}  {format_chainage(result.chainage, ch_fmt, ch_dec)}"
        if ve != 1.0:
            title += f"  (VE {ve:g}x)"
        doc.text(title, (tx(off_lo), ty(top) + 2.5 * text_h),
                 height=1.5 * text_h, layer=LAYER_TEXT)

        # optional cut/fill annotation from the comparison engine
        comp = comp_by_id.get(result.section_id)
        if comp is not None and comp.valid:
            note = (f"CUT {comp.cut_area:.2f} m2   "
                    f"FILL {comp.fill_area:.2f} m2")
            doc.text(note, (tx(off_lo), ty(top) + 0.8 * text_h),
                     height=0.8 * text_h, layer=LAYER_TEXT)

        # profile polylines per DEM, split at NaN gaps
        for layer_id, (layer_name, _aci) in layer_map.items():
            line = result.lines.get(layer_id)
            if line is None or line.elevations is None:
                continue
            offsets = np.asarray(line.offsets, dtype=float)
            elevs = np.asarray(line.elevations, dtype=float)
            xs = ox + (offsets - off_lo)
            ys = oy + (elevs - datum) * ve
            for run in _valid_runs(xs, ys):
                doc.polyline(run, layer=layer_name)

    doc.save()
    return str(path)
