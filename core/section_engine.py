# -*- coding: utf-8 -*-
"""Cross-section generation from an AlignmentDef (pure logic; no qgis
imports — operates on data_models + geometry_math only)."""
from ..constants import (DEFAULTS, MODE_INTERVAL, MODE_COUNT, MODE_LIST,
                         INCLUDE_BOTH, CHAINAGE_MERGE_TOL, TANGENT_LOCAL,
                         VERTEX_BISECTOR, VERTEX_MODES)
from . import geometry_math as gm
from .data_models import SectionDef
from .stationing import StationEquations
from .units import format_chainage


def displayed_to_distance(alignment, chainage):
    """Displayed chainage → geometric distance (kept here so this module
    stays importable without QGIS)."""
    return chainage - alignment.start_chainage


def _station_equations(settings):
    """StationEquations built from the ``station_equations`` settings list
    (list of [raw_chainage, ahead_chainage] pairs). The alignment start
    chainage is already applied by the caller, so start_offset is 0."""
    try:
        eq_list = settings.get("station_equations") or []
    except (TypeError, AttributeError):
        eq_list = []
    return StationEquations(eq_list, start_offset=0.0)


def displayed_chainage(alignment, distance, settings=None):
    """Raw distance along the alignment → displayed chainage.

    Applies the alignment start chainage, then any station equations
    from settings key ``station_equations``. Used for labels and export
    records; SectionDef.distance always stays raw.
    """
    base = float(distance) + alignment.start_chainage
    s = dict(DEFAULTS)
    s.update(settings or {})
    eq = _station_equations(s)
    return eq.apply(base) if eq else base


def _dedupe_preserve_order(values, tol=CHAINAGE_MERGE_TOL):
    """Remove chainages within tol of an earlier value while keeping the
    first-seen order (Mode C 'retain entered order' option)."""
    out = []
    for v in values:
        if all(abs(v - u) > tol for u in out):
            out.append(v)
    return out


def generate_chainages(alignment, mode, start, end, interval=None,
                       count=None, chainage_list=None,
                       include=INCLUDE_BOTH, add_vertices=False,
                       extra_chainages=None, preserve_order=False):
    """Produce the displayed-chainage series for section generation.

    start/end are displayed chainages (already clamped by the caller).
    preserve_order applies to MODE_LIST only: duplicates within
    CHAINAGE_MERGE_TOL are removed but the entered order is retained
    (no sorting).
    Returns (chainages:list[float], info:dict, warnings:list[str]).
    """
    warnings = []
    if mode == MODE_INTERVAL:
        ch, info = gm.chainages_by_interval(start, end, interval or 0.0,
                                            include)
    elif mode == MODE_COUNT:
        ch, info = gm.chainages_by_count(start, end, count or 0, include)
    elif mode == MODE_LIST:
        vals = chainage_list or []
        valid, rejected = gm.validate_chainage_list(vals, start, end)
        for v, reason in rejected:
            warnings.append(f"Chainage {v!r} rejected: {reason}.")
        if preserve_order:
            ch = _dedupe_preserve_order(valid)
        else:
            ch = gm.merge_close(valid)
        dup = len(valid) - len(ch)
        if dup:
            warnings.append(f"{dup} duplicate chainage(s) merged.")
        info = {"count": len(ch),
                "first": ch[0] if ch else None,
                "last": ch[-1] if ch else None}
    else:
        return [], {"error": f"Unknown mode {mode!r}"}, warnings
    if "error" in info:
        return [], info, warnings

    extra = list(extra_chainages or [])
    if add_vertices:
        for d in alignment.cum_dist[1:-1]:
            c = d + alignment.start_chainage
            if start - CHAINAGE_MERGE_TOL <= c <= end + CHAINAGE_MERGE_TOL:
                extra.append(c)
    if extra:
        valid, rejected = gm.validate_chainage_list(extra, start, end)
        for v, reason in rejected:
            warnings.append(f"Extra chainage {v!r} rejected: {reason}.")
        before = len(ch)
        if preserve_order and mode == MODE_LIST:
            ch = _dedupe_preserve_order(list(ch) + valid)
        else:
            ch = gm.merge_close(list(ch) + valid)
        info["count"] = len(ch)
        info["added"] = len(ch) - before
    return ch, info, warnings


def build_sections(alignment, chainages, settings=None):
    """Construct SectionDef objects at the given displayed chainages.

    settings keys (defaults from constants.DEFAULTS):
      left_width, right_width, tangent_method, tangent_avg_distance,
      vertex_handling ("bisector" | "incoming" | "outgoing" — section
      direction exactly at alignment vertices; local tangent method),
      angle_offset_deg, fixed_bearing_deg (None), reverse_normal (bool),
      section_prefix, section_start_number, section_number_padding,
      major_every, chainage_format, decimals_chainage, label_format,
      station_equations (list of [raw, ahead] pairs; when non-empty the
      displayed chainage used for labels/records is re-numbered through
      StationEquations while SectionDef.distance stays raw).

    Returns (sections:list[SectionDef], warnings:list[str]).
    """
    s = dict(DEFAULTS)
    s.update(settings or {})
    warnings = []
    sections = []
    verts, cum = alignment.vertices, alignment.cum_dist
    method = s.get("tangent_method", TANGENT_LOCAL)
    avg_d = float(s.get("tangent_avg_distance", 10.0))
    vertex_mode = s.get("vertex_handling", VERTEX_BISECTOR)
    if vertex_mode not in VERTEX_MODES:
        warnings.append(f"Unknown vertex handling {vertex_mode!r} — "
                        "using the angle bisector.")
        vertex_mode = VERTEX_BISECTOR
    fixed_bearing = s.get("fixed_bearing_deg")
    angle_off = float(s.get("angle_offset_deg", 0.0) or 0.0)
    reverse_n = bool(s.get("reverse_normal", False))
    lw = float(s.get("left_width", 25.0))
    rw = float(s.get("right_width", 25.0))
    prefix = s.get("section_prefix", "XS")
    num0 = int(s.get("section_start_number", 1))
    pad = int(s.get("section_number_padding", 2))
    major_every = int(s.get("major_every", 5) or 0)
    chfmt = s.get("chainage_format", DEFAULTS["chainage_format"])
    chdec = int(s.get("decimals_chainage", 2))
    label_fmt = s.get("label_format", DEFAULTS["label_format"])
    # Does the format vary per section? Only {number} or {chainage} make a
    # label unique; a plain prefix like "MAC-CH-" does not, so we later
    # auto-append the chainage in that case.
    _has_unique_token = ("{number}" in label_fmt
                         or "{chainage}" in label_fmt)
    # station equations: re-number displayed chainage for labels/records
    # (SectionDef.distance always stays the raw geometric distance)
    equations = _station_equations(s)
    if equations:
        warnings.extend(StationEquations.validate(
            s.get("station_equations") or []))

    for k, c in enumerate(chainages):
        d = displayed_to_distance(alignment, c)
        d = min(max(d, 0.0), alignment.length)
        center = gm.point_at(verts, cum, d)
        tangent = gm.tangent_at(verts, cum, d, method, avg_d,
                                vertex_mode=vertex_mode)
        left, right, normal = gm.section_endpoints(
            center, tangent, lw, rw, angle_offset_deg=angle_off,
            fixed_bearing_deg=fixed_bearing, reverse=reverse_n)
        num = num0 + k
        numtext = str(num).rjust(pad, "0")
        c_disp = equations.apply(d + alignment.start_chainage) \
            if equations else c
        chtext = format_chainage(c_disp, chfmt, chdec)
        try:
            label = label_fmt.format(prefix=prefix, number=numtext,
                                     chainage=chtext,
                                     alignment=alignment.name)
        except (KeyError, IndexError, ValueError, AttributeError):
            # malformed format string (unknown token, unbalanced brace…)
            # must never break a run — fall back to the standard label
            label = f"{prefix}{numtext} {chtext}"
        # If the format references neither the section number nor the
        # chainage, the SAME text repeats on every section (e.g. a plain
        # "MAC-CH-" prefix). Auto-append the chainage so every label is
        # unique and carries its station — the behaviour the user expects.
        if not _has_unique_token and chtext and chtext not in label:
            label = f"{label} {chtext}".strip()
        sec = SectionDef(
            section_id=num,
            label=label,
            chainage=c_disp,
            distance=d,
            center=center,
            tangent=tangent,
            normal=normal,
            left_width=lw,
            right_width=rw,
            angle_offset_deg=angle_off,
            is_major=(major_every > 0 and (k % major_every == 0)),
            source="generated")
        sections.append(sec)

    # ---- quality checks -----------------------------------------------
    hits = gm.find_intersecting_sections(sections)
    if hits:
        pairs = ", ".join(
            f"{sections[i].label} × {sections[j].label}"
            for i, j in hits[:10])
        more = "" if len(hits) <= 10 else f" (+{len(hits) - 10} more)"
        warnings.append(
            f"{len(hits)} pair(s) of section lines intersect: "
            f"{pairs}{more}. Consider the averaged/smoothed tangent method, "
            "shorter widths, or wider spacing at bends.")
        for i, j in hits:
            sections[i].warnings.append("Intersects a neighbouring section.")
            sections[j].warnings.append("Intersects a neighbouring section.")
    # multiple alignment crossings (hairpin bends)
    multi = gm.find_multi_crossings(verts, cum, sections)
    if multi:
        names = ", ".join(sections[i].label for i in multi[:10])
        more = "" if len(multi) <= 10 else f" (+{len(multi) - 10} more)"
        warnings.append(
            f"{len(multi)} section line(s) cross the alignment more than "
            f"once: {names}{more}. Profiles there mix two alignment "
            "passes — reduce the section widths at these bends.")
        for i in multi:
            sections[i].warnings.append(
                "Crosses the alignment more than once.")
    # degenerate (zero/near-zero length) section lines
    for i, msg in gm.validate_sections(sections):
        warnings.append(f"{sections[i].label}: {msg}")
        sections[i].warnings.append(msg)
    # rapid rotation check
    import math
    for a, b in zip(sections, sections[1:]):
        dot = a.tangent[0] * b.tangent[0] + a.tangent[1] * b.tangent[1]
        ang = math.degrees(math.acos(max(-1.0, min(1.0, dot))))
        gap = b.distance - a.distance
        if gap > 0 and ang / gap > 15.0 and ang > 30.0:
            b.warnings.append(
                f"Section direction rotates {ang:.0f}° over {gap:.1f} m — "
                "review at this bend.")
    return sections, warnings


def sections_to_wkt(sections):
    """WKT LINESTRING per section (for QGIS-free testing/export)."""
    out = []
    for sec in sections:
        lp, r = sec.left_point, sec.right_point
        out.append(
            f"LINESTRING ({lp[0]:.6f} {lp[1]:.6f}, "
            f"{sec.center[0]:.6f} {sec.center[1]:.6f}, "
            f"{r[0]:.6f} {r[1]:.6f})")
    return out
