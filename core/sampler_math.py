# -*- coding: utf-8 -*-
"""Pure numpy raster interpolation on grid blocks. No qgis imports.

Grid convention: `grid[row, col]`, row 0 at the TOP of the block
(matching QgsRasterBlock), with the block's geo-referencing described by
(origin_x, origin_y, pixel_x, pixel_y) where origin is the OUTER corner of
the top-left pixel and pixel_y is positive (northing decreases with row).
NoData must already be converted to NaN in `grid`.
"""
import numpy as np


def world_to_pixel(xs, ys, origin_x, origin_y, pixel_x, pixel_y):
    """World coordinates → fractional pixel-CENTRE coordinates (col, row)."""
    cols = (np.asarray(xs, dtype=np.float64) - origin_x) / pixel_x - 0.5
    rows = (origin_y - np.asarray(ys, dtype=np.float64)) / pixel_y - 0.5
    return cols, rows


def sample_nearest(grid, cols, rows):
    h, w = grid.shape
    c = np.rint(cols).astype(np.int64)
    r = np.rint(rows).astype(np.int64)
    out = np.full(c.shape, np.nan)
    ok = (c >= 0) & (c < w) & (r >= 0) & (r < h)
    out[ok] = grid[r[ok], c[ok]]
    return out


def sample_bilinear(grid, cols, rows):
    """Bilinear interpolation with NaN-aware fallback.

    Cells with all four neighbours valid are interpolated; cells where some
    neighbours are NoData fall back to the nearest valid neighbour of the
    four; cells with no valid neighbour return NaN.
    """
    h, w = grid.shape
    c0 = np.floor(cols).astype(np.int64)
    r0 = np.floor(rows).astype(np.int64)
    tc = cols - c0
    tr = rows - r0
    out = np.full(cols.shape, np.nan)
    inside = (cols >= -0.5) & (cols <= w - 0.5) & \
             (rows >= -0.5) & (rows <= h - 0.5)
    if not inside.any():
        return out
    c0c = np.clip(c0, 0, w - 1)
    c1c = np.clip(c0 + 1, 0, w - 1)
    r0c = np.clip(r0, 0, h - 1)
    r1c = np.clip(r0 + 1, 0, h - 1)
    q00 = grid[r0c, c0c]
    q01 = grid[r0c, c1c]
    q10 = grid[r1c, c0c]
    q11 = grid[r1c, c1c]
    top = q00 * (1 - tc) + q01 * tc
    bot = q10 * (1 - tc) + q11 * tc
    val = top * (1 - tr) + bot * tr
    all_valid = ~(np.isnan(q00) | np.isnan(q01) | np.isnan(q10) |
                  np.isnan(q11))
    ok = inside & all_valid
    out[ok] = val[ok]
    # fallback: where the 2x2 window contains NoData, take the value of the
    # nearest cell — which is NaN when the sample point itself lies within a
    # NoData pixel. Honest for holes, continuous at hole edges.
    fb = inside & ~all_valid
    if fb.any():
        out[fb] = sample_nearest(grid, cols[fb], rows[fb])
    return out


def _cubic_kernel(t):
    """Catmull-Rom weights for samples at offsets [-1, 0, 1, 2] given
    fractional position t in [0,1). Weights sum to 1; exact on linear data.

    p(t) = 0.5*(2*p0 + (-p_-1 + p1)*t + (2*p_-1 - 5*p0 + 4*p1 - p2)*t^2
                + (-p_-1 + 3*p0 - 3*p1 + p2)*t^3)
    """
    t2, t3 = t * t, t * t * t
    w0 = 0.5 * (-t + 2 * t2 - t3)          # p_-1
    w1 = 0.5 * (2 - 5 * t2 + 3 * t3)       # p_0
    w2 = 0.5 * (t + 4 * t2 - 3 * t3)       # p_1
    w3 = 0.5 * (-t2 + t3)                  # p_2
    return w0, w1, w2, w3


def sample_cubic(grid, cols, rows):
    """Catmull-Rom bicubic. Any NaN inside the 4x4 window → bilinear
    fallback for that sample (then nearest via bilinear's own fallback)."""
    h, w = grid.shape
    out = np.full(cols.shape, np.nan)
    inside = (cols >= 1) & (cols <= w - 2.001) & \
             (rows >= 1) & (rows <= h - 2.001)
    idx = np.where(inside)[0] if cols.ndim == 1 else None
    bilinear_all = sample_bilinear(grid, cols, rows)
    if not inside.any():
        return bilinear_all
    c0 = np.floor(cols[inside]).astype(np.int64)
    r0 = np.floor(rows[inside]).astype(np.int64)
    tc = cols[inside] - c0
    tr = rows[inside] - r0
    wc = _cubic_kernel(tc)
    wr = _cubic_kernel(tr)
    acc = np.zeros(c0.shape)
    nan_mask = np.zeros(c0.shape, dtype=bool)
    for j in range(4):          # rows offsets -1..2
        row_val = np.zeros(c0.shape)
        for i in range(4):      # col offsets -1..2
            g = grid[r0 + (j - 1), c0 + (i - 1)]
            nan_mask |= np.isnan(g)
            row_val += np.nan_to_num(g) * wc[i]
        acc += row_val * wr[j]
    res = np.where(nan_mask, bilinear_all[inside], acc)
    out[inside] = res
    out[~inside] = bilinear_all[~inside]
    return out


SAMPLERS = {"nearest": sample_nearest,
            "bilinear": sample_bilinear,
            "cubic": sample_cubic}


def interpolate_gaps(positions, values, max_gap):
    """Linearly fill NaN runs whose span along `positions` is <= max_gap.

    Endpoints NaN runs are never filled. Returns (filled_values, n_filled).
    """
    v = np.array(values, dtype=np.float64)
    isnan = np.isnan(v)
    if not isnan.any() or isnan.all():
        return v, 0
    n_filled = 0
    i = 0
    n = len(v)
    while i < n:
        if not isnan[i]:
            i += 1
            continue
        j = i
        while j < n and isnan[j]:
            j += 1
        if i > 0 and j < n:
            span = positions[j] - positions[i - 1]
            if span <= max_gap:
                x0, x1 = positions[i - 1], positions[j]
                y0, y1 = v[i - 1], v[j]
                seg = (np.asarray(positions[i:j]) - x0) / (x1 - x0)
                v[i:j] = y0 + seg * (y1 - y0)
                n_filled += (j - i)
        i = j
    return v, n_filled
