# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Processing algorithm: cut/fill areas per section between two DEMs.

Samples a reference and a comparison DEM along each section line and
integrates the difference (comparison − reference) over the line by the
trapezoidal rule via core.comparison_engine.cut_fill_areas. Fill is where
the comparison surface is above the reference; cut where below. NoData
spans are excluded and reported as gap_length.
"""
import math

import numpy as np
from qgis.core import (NULL, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsFeature, QgsFeatureSink,
                       QgsField, QgsFields, QgsProcessing,
                       QgsProcessingAlgorithm, QgsProcessingException,
                       QgsProcessingParameterEnum,
                       QgsProcessingParameterFeatureSink,
                       QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterNumber,
                       QgsProcessingParameterRasterLayer, QgsProject,
                       QgsWkbTypes)
from qgis.PyQt.QtCore import QCoreApplication, QVariant

from ..constants import INTERP_METHODS
from ..core.comparison_engine import cut_fill_areas
from ..core.raster_sampler import DemGridCache, SamplerError


class CompareSurfacesAlgorithm(QgsProcessingAlgorithm):
    """Cut/fill area per section between reference and comparison DEMs."""

    INPUT = "INPUT"
    REFERENCE = "REFERENCE"
    COMPARISON = "COMPARISON"
    INTERVAL = "INTERVAL"
    INTERPOLATION = "INTERPOLATION"
    OUTPUT = "OUTPUT"

    def tr(self, text):
        return QCoreApplication.translate("CompareSurfacesAlgorithm", text)

    def createInstance(self):
        return CompareSurfacesAlgorithm()

    def name(self):
        return "comparesurfaces"

    def displayName(self):
        return self.tr("Compare surfaces (cut/fill areas)")

    def group(self):
        return self.tr("Profiles")

    def groupId(self):
        return "profiles"

    def shortHelpString(self):
        return self.helpString()

    def helpString(self):
        return self.tr(
            "<p>Samples a reference DEM and a comparison DEM along each "
            "section line at a regular interval and integrates the "
            "difference (comparison minus reference) by the trapezoidal "
            "rule. Fill area is where the comparison surface lies above "
            "the reference; cut area where it lies below; net area is "
            "fill minus cut. Sample positions are distances along each "
            "line from its start vertex. Spans where either surface is "
            "NoData are excluded from the areas and reported as "
            "<i>gap_length</i>. The <i>section_id</i> and <i>chainage</i> "
            "attributes are copied from the input where those fields "
            "exist (chainage defaults to 0 when unknown). The output is a "
            "geometry-less table with one row per section.</p>")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Section lines"),
            [QgsProcessing.SourceType.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.REFERENCE, self.tr("Reference DEM")))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.COMPARISON, self.tr("Comparison DEM")))
        self.addParameter(QgsProcessingParameterNumber(
            self.INTERVAL, self.tr("Sampling interval"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=1.0,
            minValue=0.001))
        self.addParameter(QgsProcessingParameterEnum(
            self.INTERPOLATION, self.tr("Interpolation"),
            options=[self.tr("Nearest"), self.tr("Bilinear"),
                     self.tr("Cubic")],
            defaultValue=1))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Cut/fill areas"),
            QgsProcessing.SourceType.TypeVector))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.tr("Invalid section layer."))
        ref = self.parameterAsRasterLayer(parameters, self.REFERENCE,
                                          context)
        cmp_ = self.parameterAsRasterLayer(parameters, self.COMPARISON,
                                           context)
        if ref is None or not ref.isValid():
            raise QgsProcessingException(self.tr("Invalid reference DEM."))
        if cmp_ is None or not cmp_.isValid():
            raise QgsProcessingException(self.tr("Invalid comparison DEM."))
        interval = self.parameterAsDouble(parameters, self.INTERVAL, context)
        interp = INTERP_METHODS[
            self.parameterAsEnum(parameters, self.INTERPOLATION, context)]

        fields = QgsFields()
        fields.append(QgsField("section_id", QVariant.Int))
        fields.append(QgsField("chainage", QVariant.Double))
        fields.append(QgsField("cut_area", QVariant.Double))
        fields.append(QgsField("fill_area", QVariant.Double))
        fields.append(QgsField("net_area", QVariant.Double))
        fields.append(QgsField("gap_length", QVariant.Double))
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.Type.NoGeometry, QgsCoordinateReferenceSystem())
        if sink is None:
            raise QgsProcessingException(
                self.tr("Could not create the output sink."))

        try:
            caches = [DemGridCache(ref), DemGridCache(cmp_)]
        except SamplerError as exc:
            raise QgsProcessingException(str(exc))

        src_crs = source.sourceCrs()
        transforms = []
        for lyr in (ref, cmp_):
            tr = None
            if (src_crs.isValid() and lyr.crs().isValid()
                    and src_crs != lyr.crs()):
                tr = QgsCoordinateTransform(src_crs, lyr.crs(),
                                            QgsProject.instance())
            transforms.append(tr)

        id_field = source.fields().lookupField("section_id")
        ch_field = source.fields().lookupField("chainage")
        total = max(source.featureCount(), 1)
        seq_id = 0
        for fi, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            seq_id += 1
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            length = geom.length()
            if length <= 0:
                continue
            if id_field >= 0 and feat[id_field] not in (None, NULL):
                sec_id = int(feat[id_field])
            else:
                sec_id = seq_id
            chainage = 0.0
            if ch_field >= 0 and feat[ch_field] not in (None, NULL):
                try:
                    chainage = float(feat[ch_field])
                except (TypeError, ValueError):
                    chainage = 0.0

            n = int(math.floor(length / interval))
            distances = [i * interval for i in range(n + 1)]
            if distances[-1] < length - 1e-9:
                distances.append(length)
            pts = []
            dists = []
            for d in distances:
                p = geom.interpolate(float(d))
                if p is not None and not p.isEmpty():
                    pts.append(p.asPoint())
                    dists.append(float(d))
            if len(pts) < 2:
                continue
            positions = np.asarray(dists, dtype=np.float64)

            elevs = []
            for cache, tr in zip(caches, transforms):
                xs = np.array([p.x() for p in pts], dtype=np.float64)
                ys = np.array([p.y() for p in pts], dtype=np.float64)
                if tr is not None:
                    tp = [tr.transform(p) for p in pts]
                    xs = np.array([p.x() for p in tp], dtype=np.float64)
                    ys = np.array([p.y() for p in tp], dtype=np.float64)
                try:
                    cache.ensure_coverage(xs, ys)
                    elevs.append(cache.sample(xs, ys, interp))
                except SamplerError as exc:
                    raise QgsProcessingException(str(exc))

            diff = elevs[1] - elevs[0]        # comparison − reference
            cut, fill, gap = cut_fill_areas(positions, diff)

            out = QgsFeature(fields)
            out.setAttributes([sec_id, chainage, cut, fill, fill - cut,
                               gap])
            sink.addFeature(out, QgsFeatureSink.Flag.FastInsert)
            feedback.setProgress(int(100.0 * (fi + 1) / total))

        return {self.OUTPUT: dest_id}
