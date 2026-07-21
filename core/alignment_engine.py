# -*- coding: utf-8 -*-
"""Alignment resolution: QGIS features → AlignmentDef (QGIS boundary layer).

Handles feature selection modes, multipart policy, CRS transformation,
direction reversal, chainage limits and validation.
"""
import math
import logging

_LOG = logging.getLogger(__name__)

from qgis.core import QgsWkbTypes

from ..constants import (MULTIPART_SEPARATE, MULTIPART_JOIN_TOL,
                         MULTIPART_REJECT, MIN_SEGMENT_LENGTH)
from . import geometry_math as gm
from .crs_engine import transform_geometry, is_geographic
from .data_models import AlignmentDef


class AlignmentError(Exception):
    pass


def _geometry_parts(geom):
    """Extract polyline vertex lists per part from a QGIS line geometry.

    Returns a list of (xy:list[(x, y)], z:list[float] or None) tuples —
    one per part. When the geometry carries Z, per-vertex Z values are
    captured (NaN preserved as NoData); M is dropped and curves are
    segmentised.
    """
    if geom is None or geom.isEmpty():
        return []
    g = geom
    try:
        if QgsWkbTypes.isCurvedType(g.wkbType()):
            g = g.segmentize()
    except Exception:                                  # noqa: BLE001
        _LOG.debug("segmentize() failed; using original geometry",
                   exc_info=True)
    parts = []
    has_z = False
    try:
        has_z = QgsWkbTypes.hasZ(g.wkbType())
    except Exception:
        has_z = False
    if has_z:
        try:
            ag = g.constGet()
            if g.isMultipart():
                lines = [ag.geometryN(i) for i in range(ag.numGeometries())]
            else:
                lines = [ag]
            for ls in lines:
                n = ls.numPoints()
                xy, zs = [], []
                for i in range(n):
                    p = ls.pointN(i)
                    xy.append((p.x(), p.y()))
                    zs.append(float(p.z()))
                parts.append((xy, zs))
        except Exception:
            parts = []
            has_z = False
    if not has_z and not parts:
        if g.isMultipart():
            lines = g.asMultiPolyline()
        else:
            pl = g.asPolyline()
            lines = [pl] if pl else []
        for line in lines:
            parts.append(([(p.x(), p.y()) for p in line], None))
    return [(xy, z) for xy, z in parts if len(xy) >= 2]


def _clean_polyline_z(vertices, zs, min_seg=MIN_SEGMENT_LENGTH):
    """clean_polyline variant that keeps a Z list in step with the
    surviving vertices. Returns (cleaned_xy, cleaned_z, warnings)."""
    warnings = []
    if not vertices:
        return [], [], ["Empty geometry."]
    out = [tuple(map(float, vertices[0]))]
    zout = [float(zs[0])]
    removed = 0
    for (vx, vy), vz in zip(vertices[1:], zs[1:]):
        px, py = out[-1]
        if math.hypot(vx - px, vy - py) <= min_seg:
            removed += 1
            continue
        out.append((float(vx), float(vy)))
        zout.append(float(vz))
    if removed:
        warnings.append(f"{removed} duplicate/near-zero vertex(es) removed.")
    if len(out) < 2:
        warnings.append("Polyline has fewer than two distinct vertices.")
    return out, zout, warnings


def resolve_alignments(layer, features, calc_crs=None, name_field=None,
                       multipart_mode=MULTIPART_JOIN_TOL, join_tol=0.01,
                       reverse=False, start_chainage=0.0):
    """Build AlignmentDef objects from selected features.

    Returns (alignments:list[AlignmentDef], warnings:list[str]).
    Raises AlignmentError for fatal conditions (nothing usable, geographic
    CRS without a calc CRS, rejected multipart).
    """
    warnings = []
    src_crs = layer.crs()
    dst_crs = calc_crs or src_crs
    if is_geographic(dst_crs):
        raise AlignmentError(
            "Calculation CRS is geographic (degree units). Select a "
            "projected CRS before generating sections.")
    alignments = []
    for feat in features:
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            warnings.append(f"Feature {feat.id()}: empty geometry — skipped.")
            continue
        if not geom.isGeosValid():
            warnings.append(
                f"Feature {feat.id()}: geometry reported invalid by GEOS — "
                "attempting to use it anyway.")
        geom = transform_geometry(geom, src_crs, dst_crs)
        parts = _geometry_parts(geom)
        if not parts:
            warnings.append(
                f"Feature {feat.id()}: no usable line part — skipped.")
            continue
        if len(parts) > 1:
            if multipart_mode == MULTIPART_REJECT:
                raise AlignmentError(
                    f"Feature {feat.id()} is multipart ({len(parts)} parts). "
                    "Multipart geometry was set to be rejected. Choose a "
                    "different multipart handling option.")
            if multipart_mode == MULTIPART_JOIN_TOL:
                had_z = any(z is not None for _xy, z in parts)
                joined, rep = gm.join_parts([xy for xy, _z in parts],
                                            join_tol)
                parts = [(xy, None) for xy in joined]
                if had_z:
                    warnings.append(
                        f"Feature {feat.id()}: vertex Z values are not "
                        "preserved through multipart joining.")
                warnings.extend(
                    f"Feature {feat.id()}: {r}" for r in rep)
            # MULTIPART_SEPARATE (or leftovers of join): each part becomes
            # its own alignment
        if name_field and name_field in feat.fields().names():
            base_name = str(feat[name_field])
        else:
            base_name = f"Feature {feat.id()}"
        for pi, (verts, zvals) in enumerate(parts):
            if zvals is not None:
                cleaned, cleaned_z, wns = _clean_polyline_z(
                    verts, zvals, MIN_SEGMENT_LENGTH)
            else:
                cleaned, wns, _ = gm.clean_polyline(verts,
                                                    MIN_SEGMENT_LENGTH)
                cleaned_z = None
            warnings.extend(f"{base_name}: {w}" for w in wns)
            if len(cleaned) < 2:
                warnings.append(
                    f"{base_name} part {pi}: degenerate after cleaning — "
                    "skipped.")
                continue
            if reverse:
                cleaned = list(reversed(cleaned))
                if cleaned_z is not None:
                    cleaned_z = list(reversed(cleaned_z))
            name = base_name if len(parts) == 1 else f"{base_name}.{pi + 1}"
            a = AlignmentDef(
                name=name,
                layer_id=layer.id(),
                feature_id=feat.id(),
                vertices=cleaned,
                cum_dist=gm.cumulative_distances(cleaned),
                crs_authid=dst_crs.authid(),
                start_chainage=float(start_chainage),
                reversed=reverse,
                part_index=pi,
                vertex_z=cleaned_z)
            if a.length <= 0:
                warnings.append(f"{name}: zero length — skipped.")
                continue
            alignments.append(a)
    if not alignments:
        raise AlignmentError(
            "No usable alignment could be built from the selection. " +
            (" ".join(warnings[-3:]) if warnings else ""))
    return alignments, warnings


def clamp_chainage_window(alignment, proc_start=None, proc_end=None):
    """Validate/clamp a processing window in DISPLAYED chainage.

    Returns (start_disp, end_disp, warnings). Falls back to the full
    alignment when values are missing or invalid.
    """
    warnings = []
    a0, a1 = alignment.start_chainage, alignment.end_chainage
    s = a0 if proc_start is None else float(proc_start)
    e = a1 if proc_end is None else float(proc_end)
    if s < a0:
        warnings.append(
            f"Start chainage {s:.3f} is before the alignment start "
            f"({a0:.3f}); clamped.")
        s = a0
    if e > a1:
        warnings.append(
            f"End chainage {e:.3f} is beyond the alignment end "
            f"({a1:.3f}); clamped.")
        e = a1
    if e <= s:
        warnings.append(
            "End chainage must exceed start chainage; using the full "
            "alignment instead.")
        s, e = a0, a1
    return s, e, warnings


def displayed_to_distance(alignment, chainage):
    """Displayed chainage → geometric distance along the polyline."""
    return chainage - alignment.start_chainage


def distance_to_displayed(alignment, distance):
    return distance + alignment.start_chainage
