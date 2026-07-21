# -*- coding: utf-8 -*-
"""Surface comparison: difference profiles and cut/fill areas.

Pure numpy — no qgis imports. Sign convention: FILL where the comparison
surface is ABOVE the reference surface; CUT where below. The UI labels the
convention explicitly and allows swapping surfaces.
"""
import numpy as np

from .data_models import SectionComparison


def difference(ref_elev, cmp_elev):
    """Elementwise (comparison - reference); NaN where either is NaN."""
    r = np.asarray(ref_elev, dtype=np.float64)
    c = np.asarray(cmp_elev, dtype=np.float64)
    return c - r


def cut_fill_areas(offsets, diff):
    """Integrate a difference profile over offset by the trapezoidal rule,
    splitting positive (fill) and negative (cut) parts exactly at
    zero-crossings. NaN spans are excluded and their offset length returned.

    Returns (cut_area, fill_area, gap_length) — areas are positive numbers.
    """
    x = np.asarray(offsets, dtype=np.float64)
    d = np.asarray(diff, dtype=np.float64)
    if len(x) < 2:
        return 0.0, 0.0, 0.0
    cut = fill = gap = 0.0
    for i in range(len(x) - 1):
        x0, x1 = x[i], x[i + 1]
        y0, y1 = d[i], d[i + 1]
        w = x1 - x0
        if w <= 0:
            continue
        if np.isnan(y0) or np.isnan(y1):
            gap += w
            continue
        if y0 >= 0 and y1 >= 0:
            fill += 0.5 * (y0 + y1) * w
        elif y0 <= 0 and y1 <= 0:
            cut += -0.5 * (y0 + y1) * w
        else:
            # zero crossing at xc
            t = y0 / (y0 - y1)
            wc = w * t
            if y0 > 0:
                fill += 0.5 * y0 * wc
                cut += -0.5 * y1 * (w - wc)
            else:
                cut += -0.5 * y0 * wc
                fill += 0.5 * y1 * (w - wc)
    return float(cut), float(fill), float(gap)


def compare_section(profile, ref_layer_id, cmp_layer_id):
    """Build a SectionComparison from a ProfileResult."""
    ref = profile.lines.get(ref_layer_id)
    cmp_ = profile.lines.get(cmp_layer_id)
    sc = SectionComparison(section_id=profile.section_id or 0,
                           label=profile.label,
                           chainage=profile.chainage)
    if ref is None or cmp_ is None or ref.elevations is None \
            or cmp_.elevations is None:
        sc.valid = False
        return sc
    d = difference(ref.elevations, cmp_.elevations)
    cut, fill, gap = cut_fill_areas(profile.offsets, d)
    sc.cut_area, sc.fill_area, sc.gap_length = cut, fill, gap
    total = float(profile.offsets[-1] - profile.offsets[0]) \
        if len(profile.offsets) > 1 else 0.0
    sc.valid = total > 0 and gap < total
    return sc


def threshold_exceedances(offsets, diff, tolerance):
    """Offset spans where |diff| exceeds tolerance.

    Returns list of (start_offset, end_offset, max_abs_diff).
    """
    x = np.asarray(offsets, dtype=np.float64)
    d = np.asarray(diff, dtype=np.float64)
    exceed = np.abs(d) > tolerance
    exceed &= ~np.isnan(d)
    spans = []
    i = 0
    n = len(x)
    while i < n:
        if not exceed[i]:
            i += 1
            continue
        j = i
        while j < n and exceed[j]:
            j += 1
        spans.append((float(x[i]), float(x[j - 1]),
                      float(np.nanmax(np.abs(d[i:j])))))
        i = j
    return spans
