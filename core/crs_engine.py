# -*- coding: utf-8 -*-
"""CRS validation, suggestion and transformation helpers (QGIS boundary)."""
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsProject)


def is_geographic(crs):
    return bool(crs and crs.isValid() and crs.isGeographic())


def suggest_projected_crs(layer):
    """Suggest a projected CRS for a layer in a geographic CRS.

    Uses the layer extent centroid to pick the UTM zone (WGS84-based).
    Returns QgsCoordinateReferenceSystem (invalid CRS when no suggestion).
    """
    crs = layer.crs()
    if not is_geographic(crs):
        return crs
    ext = layer.extent()
    if ext.isEmpty():
        return QgsCoordinateReferenceSystem()
    # centroid to WGS84
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    try:
        tr = QgsCoordinateTransform(crs, wgs, QgsProject.instance())
        c = tr.transform(ext.center())
        lon, lat = c.x(), c.y()
    except Exception:
        return QgsCoordinateReferenceSystem()
    zone = int((lon + 180.0) / 6.0) + 1
    zone = min(max(zone, 1), 60)
    epsg = (32600 if lat >= 0 else 32700) + zone
    return QgsCoordinateReferenceSystem(f"EPSG:{epsg}")


def make_transform(src_crs, dst_crs):
    """Transform or None when CRSs match/invalid."""
    if not src_crs.isValid() or not dst_crs.isValid() or src_crs == dst_crs:
        return None
    return QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())


def transform_geometry(geom, src_crs, dst_crs):
    """Return a transformed copy of geom (or the original when no-op)."""
    tr = make_transform(src_crs, dst_crs)
    if tr is None:
        return geom
    g = type(geom)(geom)  # copy
    g.transform(tr)
    return g
