# -*- coding: utf-8 -*-
"""Pure polyline / section mathematics. No qgis imports — fully unit-testable.

Conventions
-----------
* vertices: list[(x, y)] of a single-part polyline.
* distance d: geometric distance along the polyline from its first vertex.
* tangent: unit vector pointing in the direction of increasing distance.
* normal: tangent rotated +90° (counter-clockwise) = LEFT side of travel.
* offsets: signed; negative = left, positive = right (display convention
  applied later).
"""
import math

from ..constants import (INCLUDE_BOTH, INCLUDE_START, INCLUDE_END,
                         INCLUDE_NONE, MIN_SEGMENT_LENGTH,
                         CHAINAGE_MERGE_TOL, TANGENT_LOCAL, TANGENT_AVERAGED,
                         TANGENT_SMOOTHED, VERTEX_BISECTOR, VERTEX_INCOMING,
                         VERTEX_OUTGOING)


# --------------------------------------------------------------------------- #
# polyline basics
# --------------------------------------------------------------------------- #
def clean_polyline(vertices, min_seg=MIN_SEGMENT_LENGTH):
    """Remove duplicate and near-zero-length segments.

    Returns (cleaned_vertices, warnings:list[str], removed_count:int).
    """
    warnings = []
    if not vertices:
        return [], ["Empty geometry."], 0
    out = [tuple(map(float, vertices[0]))]
    removed = 0
    for vx, vy in vertices[1:]:
        px, py = out[-1]
        if math.hypot(vx - px, vy - py) <= min_seg:
            removed += 1
            continue
        out.append((float(vx), float(vy)))
    if removed:
        warnings.append(
            f"{removed} duplicate/near-zero vertex(es) removed.")
    if len(out) < 2:
        warnings.append("Polyline has fewer than two distinct vertices.")
    return out, warnings, removed


def cumulative_distances(vertices):
    """Cumulative distance at each vertex, starting at 0.0."""
    cum = [0.0]
    for i in range(1, len(vertices)):
        x0, y0 = vertices[i - 1]
        x1, y1 = vertices[i]
        cum.append(cum[-1] + math.hypot(x1 - x0, y1 - y0))
    return cum


def total_length(vertices):
    return cumulative_distances(vertices)[-1] if len(vertices) > 1 else 0.0


def _locate(cum, d):
    """Find segment index i such that cum[i] <= d <= cum[i+1] (clamped)."""
    if d <= 0.0:
        return 0, 0.0
    if d >= cum[-1]:
        i = len(cum) - 2
        return i, 1.0
    lo, hi = 0, len(cum) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cum[mid] <= d:
            lo = mid
        else:
            hi = mid
    seg_len = cum[lo + 1] - cum[lo]
    t = 0.0 if seg_len <= 0 else (d - cum[lo]) / seg_len
    return lo, t


def point_at(vertices, cum, d):
    """Interpolated (x, y) at distance d (clamped to the line)."""
    i, t = _locate(cum, d)
    x0, y0 = vertices[i]
    x1, y1 = vertices[i + 1]
    return (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)


def _segment_dir(vertices, i):
    x0, y0 = vertices[i]
    x1, y1 = vertices[i + 1]
    L = math.hypot(x1 - x0, y1 - y0)
    if L <= 0:
        return (1.0, 0.0)
    return ((x1 - x0) / L, (y1 - y0) / L)


def _normalise(vx, vy):
    L = math.hypot(vx, vy)
    if L <= 0:
        return (1.0, 0.0)
    return (vx / L, vy / L)


# --------------------------------------------------------------------------- #
# tangents
# --------------------------------------------------------------------------- #
def tangent_local(vertices, cum, d, vertex_tol=1e-9,
                  vertex_mode=VERTEX_BISECTOR):
    """Tangent of the containing segment.

    At an interior vertex the direction is chosen by ``vertex_mode``:

    * ``"bisector"`` (default) — normalised average of the two adjacent
      segment directions (angle bisector); avoids overlapping sections
      at sharp bends.
    * ``"incoming"`` — direction of the segment arriving at the vertex.
    * ``"outgoing"`` — direction of the segment leaving the vertex.
    """
    i, t = _locate(cum, d)
    at_vertex = None
    if t <= vertex_tol and i > 0:
        at_vertex = i
    elif t >= 1.0 - vertex_tol and i < len(vertices) - 2:
        at_vertex = i + 1
    if at_vertex is not None:
        d0 = _segment_dir(vertices, at_vertex - 1)
        d1 = _segment_dir(vertices, at_vertex)
        if vertex_mode == VERTEX_INCOMING:
            return d0
        if vertex_mode == VERTEX_OUTGOING:
            return d1
        sx, sy = d0[0] + d1[0], d0[1] + d1[1]
        if math.hypot(sx, sy) < 1e-9:          # 180° reversal — keep incoming
            return d0
        return _normalise(sx, sy)
    return _segment_dir(vertices, i)


def tangent_averaged(vertices, cum, d, half_distance):
    """Direction from point(d-h) to point(d+h), clamped to the line ends."""
    h = max(half_distance, MIN_SEGMENT_LENGTH)
    a = point_at(vertices, cum, max(0.0, d - h))
    b = point_at(vertices, cum, min(cum[-1], d + h))
    vx, vy = b[0] - a[0], b[1] - a[1]
    if math.hypot(vx, vy) < 1e-9:
        return tangent_local(vertices, cum, d)
    return _normalise(vx, vy)


def tangent_smoothed(vertices, cum, d, window):
    """Gaussian-weighted mean of sampled local tangents across ±window."""
    w = max(window, MIN_SEGMENT_LENGTH)
    n = 9
    sigma = w / 2.0
    sx = sy = 0.0
    for k in range(n):
        dk = d - w + (2.0 * w) * k / (n - 1)
        dk = min(max(dk, 0.0), cum[-1])
        tx, ty = tangent_local(vertices, cum, dk)
        g = math.exp(-((dk - d) ** 2) / (2 * sigma * sigma))
        sx += tx * g
        sy += ty * g
    if math.hypot(sx, sy) < 1e-9:
        return tangent_local(vertices, cum, d)
    return _normalise(sx, sy)


def tangent_at(vertices, cum, d, method=TANGENT_LOCAL, avg_distance=10.0,
               vertex_mode=VERTEX_BISECTOR):
    """Tangent at distance d. ``vertex_mode`` selects the direction used
    exactly at interior vertices ("bisector" | "incoming" | "outgoing");
    it only affects the local method, since the averaged/smoothed methods
    blend directions across the vertex anyway."""
    if method == TANGENT_AVERAGED:
        return tangent_averaged(vertices, cum, d, avg_distance / 2.0)
    if method == TANGENT_SMOOTHED:
        return tangent_smoothed(vertices, cum, d, avg_distance / 2.0)
    return tangent_local(vertices, cum, d, vertex_mode=vertex_mode)


def left_normal(tangent):
    """Rotate tangent +90° CCW → unit vector pointing LEFT of travel."""
    return (-tangent[1], tangent[0])


# --------------------------------------------------------------------------- #
# chainage series generation
# --------------------------------------------------------------------------- #
def chainages_by_interval(start, end, interval, include=INCLUDE_BOTH,
                          tol=CHAINAGE_MERGE_TOL):
    """Chainages from start stepping by interval, honouring inclusion flags.

    Returns (chainages:list[float], info:dict) — info reports count, first,
    last and remainder distance after the last regular chainage.
    """
    if interval <= 0 or end <= start:
        return [], {"error": "Interval must be > 0 and end > start."}
    ch = []
    n = int(math.floor((end - start) / interval + tol))
    for k in range(n + 1):
        c = start + k * interval
        if c > end + tol:
            break
        ch.append(min(c, end))
    if not ch or abs(ch[0] - start) > tol:
        ch.insert(0, start)
    if abs(ch[-1] - end) > tol:
        last_regular = ch[-1]
        if include in (INCLUDE_BOTH, INCLUDE_END):
            ch.append(end)
        remainder = end - last_regular
    else:
        remainder = 0.0
    if include in (INCLUDE_END, INCLUDE_NONE) and abs(ch[0] - start) <= tol:
        ch.pop(0)
    if include in (INCLUDE_START, INCLUDE_NONE) and ch and \
            abs(ch[-1] - end) <= tol:
        ch.pop()
    ch = merge_close(ch, tol)
    info = {"count": len(ch),
            "first": ch[0] if ch else None,
            "last": ch[-1] if ch else None,
            "remainder": remainder}
    return ch, info


def chainages_by_count(start, end, count, include=INCLUDE_BOTH):
    """Exactly `count` chainages between start and end.

    Inclusion semantics: the count is the number of sections produced.
    * both:  count sections including both ends → interval = L/(count-1)
    * start: includes start, excludes end       → interval = L/count
    * end:   excludes start, includes end       → interval = L/count
    * none:  excludes both                       → interval = L/(count+1)
    """
    L = end - start
    if L <= 0 or count < 1:
        return [], {"error": "End must exceed start and count >= 1."}
    if include == INCLUDE_BOTH:
        if count == 1:
            return [start], {"count": 1, "interval": 0.0,
                             "first": start, "last": start}
        interval = L / (count - 1)
        ch = [start + k * interval for k in range(count)]
    elif include == INCLUDE_START:
        interval = L / count
        ch = [start + k * interval for k in range(count)]
    elif include == INCLUDE_END:
        interval = L / count
        ch = [start + (k + 1) * interval for k in range(count)]
    else:
        interval = L / (count + 1)
        ch = [start + (k + 1) * interval for k in range(count)]
    ch[-1] = min(ch[-1], end)
    return ch, {"count": len(ch), "interval": interval,
                "first": ch[0], "last": ch[-1]}


def validate_chainage_list(values, start, end, tol=CHAINAGE_MERGE_TOL):
    """Filter a raw chainage list against alignment limits.

    Returns (valid:list[float], rejected:list[(value, reason)]).
    """
    valid, rejected = [], []
    for v in values:
        if v is None:
            rejected.append((v, "not a number"))
        elif v < start - tol:
            rejected.append((v, f"before start ({start:.3f})"))
        elif v > end + tol:
            rejected.append((v, f"beyond end ({end:.3f})"))
        else:
            valid.append(min(max(v, start), end))
    return valid, rejected


def merge_close(chainages, tol=CHAINAGE_MERGE_TOL):
    """Sort and merge chainages closer than tol."""
    out = []
    for c in sorted(chainages):
        if not out or c - out[-1] > tol:
            out.append(c)
    return out


# --------------------------------------------------------------------------- #
# section construction & checks
# --------------------------------------------------------------------------- #
def rotate(vec, angle_deg):
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return (vec[0] * c - vec[1] * s, vec[0] * s + vec[1] * c)


def section_endpoints(center, tangent, left_w, right_w, angle_offset_deg=0.0,
                      fixed_bearing_deg=None, reverse=False):
    """Left and right endpoints of a section line.

    fixed_bearing_deg: geographic bearing (0=N, 90=E) of the section line
    itself; overrides perpendicularity when given.
    Returns (left_xy, right_xy, normal_used).
    """
    if fixed_bearing_deg is not None:
        b = math.radians(fixed_bearing_deg)
        line_dir = (math.sin(b), math.cos(b))     # bearing → unit vector
        normal = line_dir                          # left endpoint along dir
    else:
        normal = left_normal(tangent)
        if angle_offset_deg:
            normal = rotate(normal, angle_offset_deg)
    if reverse:
        normal = (-normal[0], -normal[1])
    left = (center[0] + normal[0] * left_w, center[1] + normal[1] * left_w)
    right = (center[0] - normal[0] * right_w, center[1] - normal[1] * right_w)
    return left, right, normal


def segments_intersect(p1, p2, p3, p4, eps=1e-12):
    """True when segment p1-p2 properly intersects p3-p4."""
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < eps else (1 if v > 0 else -1)
    o1, o2 = orient(p1, p2, p3), orient(p1, p2, p4)
    o3, o4 = orient(p3, p4, p1), orient(p3, p4, p2)
    if o1 != o2 and o3 != o4:
        return True
    return False


def find_intersecting_sections(sections):
    """Detect pairs of section lines that cross.

    `sections` is a list of objects exposing .distance, .left_point,
    .right_point, .left_width, .right_width. Only chainage-neighbouring
    candidates are tested (sections further apart along the alignment than
    the sum of their reach cannot cross), keeping this O(n·k).
    Returns list of (i, j) index pairs.
    """
    hits = []
    n = len(sections)
    for i in range(n):
        si = sections[i]
        reach_i = max(si.left_width, si.right_width)
        j = i + 1
        while j < n:
            sj = sections[j]
            if (sj.distance - si.distance) > reach_i + \
                    max(sj.left_width, sj.right_width):
                break
            if segments_intersect(si.left_point, si.right_point,
                                  sj.left_point, sj.right_point):
                hits.append((i, j))
            j += 1
    return hits


def find_multi_crossings(alignment_vertices, cum, sections):
    """Indices of sections whose FULL line crosses the alignment more
    than once.

    Each section line (left endpoint → right endpoint) is tested against
    every alignment segment with :func:`segments_intersect`; a count
    above one flags the section (typical at hairpin bends where a wide
    section re-crosses the return leg — the sampled profile then mixes
    two alignment passes).

    ``cum`` is accepted for signature symmetry with the other checks and
    reserved for future range culling; it is not currently used.
    Returns a list of section indices (into ``sections``).
    """
    _ = cum
    flagged = []
    n_seg = len(alignment_vertices) - 1
    if n_seg < 1:
        return flagged
    for idx, sec in enumerate(sections):
        p1, p2 = sec.left_point, sec.right_point
        crossings = 0
        for i in range(n_seg):
            if segments_intersect(p1, p2, alignment_vertices[i],
                                  alignment_vertices[i + 1]):
                crossings += 1
                if crossings > 1:
                    break
        if crossings > 1:
            flagged.append(idx)
    return flagged


def validate_sections(sections, min_length=2 * MIN_SEGMENT_LENGTH):
    """Degenerate-geometry check for section lines.

    Flags zero/near-zero length section lines (full left-to-right length
    below ``min_length``, default 2 × MIN_SEGMENT_LENGTH) which cannot be
    sampled meaningfully. Returns a list of (index, warning:str) tuples.
    """
    warnings = []
    for idx, sec in enumerate(sections):
        lp, r = sec.left_point, sec.right_point
        length = math.hypot(r[0] - lp[0], r[1] - lp[1])
        if length < min_length:
            warnings.append(
                (idx, f"Section line is degenerate (length "
                      f"{length:.3g} m < {min_length:.3g} m)."))
    return warnings


def join_parts(parts, tol):
    """Join multipart polylines whose endpoints coincide within tol.

    Returns (joined:list[list[(x,y)]], report:list[str]). Parts that chain
    end-to-start (in either orientation) are merged; ambiguous junctions
    (three or more parts meeting) are reported and left separate.
    """
    parts = [list(p) for p in parts if len(p) >= 2]
    report = []
    if not parts:
        return [], ["No usable line parts."]
    changed = True
    while changed:
        changed = False
        for i in range(len(parts)):
            if changed:
                break
            for j in range(i + 1, len(parts)):
                a, b = parts[i], parts[j]
                pairs = [
                    ("ae_bs", a[-1], b[0], a + b[1:]),
                    ("ae_be", a[-1], b[-1], a + list(reversed(b))[1:]),
                    ("as_bs", a[0], b[0], list(reversed(b)) + a[1:]),
                    ("as_be", a[0], b[-1], b + a[1:]),
                ]
                for _, pa, pb, merged in pairs:
                    if math.hypot(pa[0] - pb[0], pa[1] - pb[1]) <= tol:
                        parts[i] = merged
                        parts.pop(j)
                        changed = True
                        break
                if changed:
                    break
    if len(parts) > 1:
        report.append(
            f"{len(parts)} disconnected part(s) remain after joining "
            f"(tolerance {tol}).")
    return parts, report
