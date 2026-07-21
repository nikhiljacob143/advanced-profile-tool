# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""GIS vector exports: memory layers and GeoPackage writing.

Boundary module — this is one of the few export modules that genuinely
requires QGIS, converting the plain data models (SectionDef,
ProfileResult) into QGIS vector layers and writing them to GeoPackage.

Offsets follow the plugin convention: negative = left of the alignment,
positive = right. NaN elevations become NULL attribute values.
"""
import math
import os
import re

from qgis.core import (QgsCoordinateTransformContext, QgsFeature, QgsField,
                       QgsGeometry, QgsPointXY, QgsVectorFileWriter,
                       QgsVectorLayer)
from qgis.PyQt.QtCore import QVariant

__all__ = ["sections_to_memory_layer", "sample_points_to_memory_layer",
           "difference_points_to_memory_layer", "write_vector", "write_gpkg"]

# QgsVectorFileWriter driver names by plugin vector_format setting value
_DRIVERS = {"gpkg": "GPKG", "shp": "ESRI Shapefile", "geojson": "GeoJSON"}


def _field_name(name, used):
    """Return a valid, unique attribute name derived from ``name``."""
    base = re.sub(r"[^A-Za-z0-9_]+", "_", str(name)).strip("_") or "dem"
    candidate = base
    n = 1
    while candidate.lower() in used:
        candidate = f"{base}_{n}"
        n += 1
    used.add(candidate.lower())
    return candidate


def _make_layer(geom_type, crs_authid, name, fields):
    """Create an editable memory layer with the given fields."""
    layer = QgsVectorLayer(f"{geom_type}?crs={crs_authid}", name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes(fields)
    layer.updateFields()
    return layer, provider


def sections_to_memory_layer(sections, crs_authid, name="Sections"):
    """Build a memory line layer of cross-section lines.

    Fields: section_id (int), label (str), chainage (double), is_major
    (int, 0/1), left_width (double), right_width (double) and warnings
    (str, "; "-joined). Geometry is the 3-point line left end → centre →
    right end so the alignment intersection vertex is preserved.
    Returns the QgsVectorLayer.
    """
    fields = [QgsField("section_id", QVariant.Int),
              QgsField("label", QVariant.String),
              QgsField("chainage", QVariant.Double),
              QgsField("is_major", QVariant.Int),
              QgsField("left_width", QVariant.Double),
              QgsField("right_width", QVariant.Double),
              QgsField("warnings", QVariant.String)]
    layer, provider = _make_layer("LineString", crs_authid, name, fields)

    features = []
    for s in sections:
        feat = QgsFeature(layer.fields())
        left = s.left_point
        right = s.right_point
        feat.setGeometry(QgsGeometry.fromPolylineXY(
            [QgsPointXY(left[0], left[1]),
             QgsPointXY(s.center[0], s.center[1]),
             QgsPointXY(right[0], right[1])]))
        feat.setAttributes([
            int(s.section_id),
            s.label,
            float(s.chainage),
            1 if s.is_major else 0,
            float(s.left_width),
            float(s.right_width),
            "; ".join(s.warnings) if s.warnings else "",
        ])
        features.append(feat)
    provider.addFeatures(features)
    layer.updateExtents()
    return layer


def sample_points_to_memory_layer(profile_results, dem_defs, crs_authid,
                                  name="Profile points"):
    """Build a memory point layer of all profile samples.

    Fields: section_id (int), label (str), chainage (double), offset
    (double, negative = left) plus one double field per enabled DEM
    (named from ``dem.name``); NaN elevations become NULL. Returns the
    QgsVectorLayer.
    """
    dems = [d for d in dem_defs if getattr(d, "enabled", True)]
    used = {"section_id", "label", "chainage", "offset"}
    dem_fields = [(d.layer_id, _field_name(d.name, used)) for d in dems]

    fields = [QgsField("section_id", QVariant.Int),
              QgsField("label", QVariant.String),
              QgsField("chainage", QVariant.Double),
              QgsField("offset", QVariant.Double)]
    fields += [QgsField(fname, QVariant.Double) for _lid, fname in dem_fields]
    layer, provider = _make_layer("Point", crs_authid, name, fields)

    features = []
    for result in profile_results:
        n = len(result.offsets) if result.offsets is not None else 0
        section_id = -1 if result.section_id is None else int(result.section_id)
        for i in range(n):
            feat = QgsFeature(layer.fields())
            feat.setGeometry(QgsGeometry.fromPointXY(
                QgsPointXY(float(result.xs[i]), float(result.ys[i]))))
            attrs = [section_id, result.label, float(result.chainage),
                     float(result.offsets[i])]
            for layer_id, _fname in dem_fields:
                line = result.lines.get(layer_id)
                if line is None or line.elevations is None:
                    attrs.append(None)
                    continue
                z = float(line.elevations[i])
                attrs.append(None if math.isnan(z) else z)
            feat.setAttributes(attrs)
            features.append(feat)
    provider.addFeatures(features)
    layer.updateExtents()
    return layer


def difference_points_to_memory_layer(profile_results, ref_id, cmp_id,
                                      crs_authid,
                                      name="Difference points"):
    """Build a memory point layer of per-sample surface differences.

    Fields: section_id (int), label (str), chainage (double), offset
    (double, negative = left), ref_z (double), cmp_z (double) and dz
    (double, cmp_z − ref_z; positive = fill, comparison above reference).
    Elevation/dz cells are NULL where either surface is NaN (NoData).
    ``ref_id``/``cmp_id`` are the DEM layer ids keying
    ``ProfileResult.lines``. Returns the QgsVectorLayer.
    """
    fields = [QgsField("section_id", QVariant.Int),
              QgsField("label", QVariant.String),
              QgsField("chainage", QVariant.Double),
              QgsField("offset", QVariant.Double),
              QgsField("ref_z", QVariant.Double),
              QgsField("cmp_z", QVariant.Double),
              QgsField("dz", QVariant.Double)]
    layer, provider = _make_layer("Point", crs_authid, name, fields)

    features = []
    for result in profile_results:
        n = len(result.offsets) if result.offsets is not None else 0
        section_id = -1 if result.section_id is None \
            else int(result.section_id)
        ref_line = result.lines.get(ref_id)
        cmp_line = result.lines.get(cmp_id)
        for i in range(n):
            feat = QgsFeature(layer.fields())
            feat.setGeometry(QgsGeometry.fromPointXY(
                QgsPointXY(float(result.xs[i]), float(result.ys[i]))))
            ref_z = cmp_z = None
            if ref_line is not None and ref_line.elevations is not None:
                v = float(ref_line.elevations[i])
                ref_z = None if math.isnan(v) else v
            if cmp_line is not None and cmp_line.elevations is not None:
                v = float(cmp_line.elevations[i])
                cmp_z = None if math.isnan(v) else v
            dz = None if (ref_z is None or cmp_z is None) \
                else (cmp_z - ref_z)
            feat.setAttributes([section_id, result.label,
                                float(result.chainage),
                                float(result.offsets[i]),
                                ref_z, cmp_z, dz])
            features.append(feat)
    provider.addFeatures(features)
    layer.updateExtents()
    return layer


def write_vector(layer, path, layer_name, fmt="gpkg"):
    """Write ``layer`` to ``path`` in the requested vector format.

    ``fmt`` is one of "gpkg", "shp" or "geojson" (plugin setting
    ``vector_format``). GeoPackage targets create-or-overwrite the named
    layer inside an existing file (other layers preserved); Shapefile and
    GeoJSON targets overwrite the whole file (single-layer formats —
    ``layer_name`` is recorded in the writer options but the file name
    governs). Returns (ok: bool, error_message: str).
    """
    driver = _DRIVERS.get(str(fmt).lower())
    if driver is None:
        return False, (f"Unsupported vector format {fmt!r}; expected one "
                       f"of {sorted(_DRIVERS)}.")
    path = str(path)
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = driver
    options.layerName = layer_name
    if driver == "GPKG" and os.path.exists(path):
        options.actionOnExistingFile = (
            QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer)
    else:
        options.actionOnExistingFile = (
            QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile)
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, path, QgsCoordinateTransformContext(), options)
    error_code, error_message = result[0], result[1]
    ok = error_code == QgsVectorFileWriter.WriterError.NoError
    return ok, ("" if ok else str(error_message))


def write_gpkg(layer, gpkg_path, layer_name):
    """Write ``layer`` into a GeoPackage as ``layer_name``.

    Thin wrapper over :func:`write_vector` with fmt="gpkg". When the
    GeoPackage already exists the layer is created or overwritten within
    it (other layers are preserved); otherwise the file is created.
    Returns (ok: bool, error_message: str).
    """
    return write_vector(layer, gpkg_path, layer_name, fmt="gpkg")
