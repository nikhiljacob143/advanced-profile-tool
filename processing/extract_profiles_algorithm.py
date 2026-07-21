# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Processing algorithm: extract elevation profiles along section lines.

Densifies each input line at a regular interval and samples one DEM at
each point via core.raster_sampler.DemGridCache. The ``distance`` field
records the distance along the section line measured from the line START
(0) to its END (line length) — for lines produced by the Generate cross
sections algorithm the start is the LEFT end of the section. NoData
samples are written with a NULL elevation.
"""
import math

import numpy as np
from qgis.core import (NULL, QgsCoordinateTransform, QgsFeature,
                       QgsFeatureSink, QgsField, QgsFields, QgsGeometry,
                       QgsProcessing, QgsProcessingAlgorithm,
                       QgsProcessingException, QgsProcessingParameterBand,
                       QgsProcessingParameterEnum,
                       QgsProcessingParameterFeatureSink,
                       QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterNumber,
                       QgsProcessingParameterRasterLayer, QgsProject,
                       QgsWkbTypes)
from qgis.PyQt.QtCore import QCoreApplication, QVariant

from ..constants import INTERP_METHODS
from ..core.raster_sampler import DemGridCache, SamplerError


class ExtractProfilesAlgorithm(QgsProcessingAlgorithm):
    """Sample a DEM along section lines at a regular interval."""

    INPUT = "INPUT"
    DEM = "DEM"
    INTERVAL = "INTERVAL"
    INTERPOLATION = "INTERPOLATION"
    BAND = "BAND"
    OUTPUT = "OUTPUT"

    def tr(self, text):
        return QCoreApplication.translate("ExtractProfilesAlgorithm", text)

    def createInstance(self):
        return ExtractProfilesAlgorithm()

    def name(self):
        return "extractprofiles"

    def displayName(self):
        return self.tr("Extract profiles from DEM")

    def group(self):
        return self.tr("Profiles")

    def groupId(self):
        return "profiles"

    def shortHelpString(self):
        return self.helpString()

    def helpString(self):
        return self.tr(
            "<p>Samples a DEM along each input line at a regular sampling "
            "interval and writes one point per sample. The <i>distance</i> "
            "attribute is measured along the line from its start vertex "
            "(0) to its end vertex (line length); for sections generated "
            "by this plugin the start vertex is the left end of the "
            "section. The <i>section_id</i> attribute is copied from the "
            "input where the field exists, otherwise a sequential id is "
            "assigned. Elevations are sampled with the selected "
            "interpolation method (nearest, bilinear or cubic); NoData "
            "samples are written with a NULL elevation. Points falling "
            "outside the DEM extent also return NULL. The line layer and "
            "DEM may be in different CRSs — sample points are transformed "
            "to the DEM CRS before sampling.</p>")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Section lines"),
            [QgsProcessing.SourceType.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, self.tr("DEM raster")))
        self.addParameter(QgsProcessingParameterNumber(
            self.INTERVAL, self.tr("Sampling interval"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=1.0,
            minValue=0.001))
        self.addParameter(QgsProcessingParameterEnum(
            self.INTERPOLATION, self.tr("Interpolation"),
            options=[self.tr("Nearest"), self.tr("Bilinear"),
                     self.tr("Cubic")],
            defaultValue=1))
        self.addParameter(QgsProcessingParameterBand(
            self.BAND, self.tr("Band"), defaultValue=1,
            parentLayerParameterName=self.DEM))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Profile points"),
            QgsProcessing.SourceType.TypeVectorPoint))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.tr("Invalid section layer."))
        dem = self.parameterAsRasterLayer(parameters, self.DEM, context)
        if dem is None or not dem.isValid():
            raise QgsProcessingException(self.tr("Invalid DEM layer."))
        interval = self.parameterAsDouble(parameters, self.INTERVAL, context)
        interp = INTERP_METHODS[
            self.parameterAsEnum(parameters, self.INTERPOLATION, context)]
        band = self.parameterAsInt(parameters, self.BAND, context)

        fields = QgsFields()
        fields.append(QgsField("section_id", QVariant.Int))
        fields.append(QgsField("distance", QVariant.Double))
        fields.append(QgsField("elevation", QVariant.Double))
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.Type.Point, source.sourceCrs())
        if sink is None:
            raise QgsProcessingException(
                self.tr("Could not create the output sink."))

        try:
            cache = DemGridCache(dem, band)
        except SamplerError as exc:
            raise QgsProcessingException(str(exc))

        transform = None
        src_crs = source.sourceCrs()
        if (src_crs.isValid() and dem.crs().isValid()
                and src_crs != dem.crs()):
            transform = QgsCoordinateTransform(src_crs, dem.crs(),
                                               QgsProject.instance())

        id_field = source.fields().lookupField("section_id")
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

            n = int(math.floor(length / interval))
            distances = [i * interval for i in range(n + 1)]
            if distances[-1] < length - 1e-9:
                distances.append(length)
            pts = []
            for d in distances:
                p = geom.interpolate(float(d))
                if p is None or p.isEmpty():
                    pts.append(None)
                else:
                    pts.append(p.asPoint())
            valid = [(d, p) for d, p in zip(distances, pts) if p is not None]
            if not valid:
                continue

            xs = np.array([p.x() for _, p in valid], dtype=np.float64)
            ys = np.array([p.y() for _, p in valid], dtype=np.float64)
            if transform is not None:
                tp = [transform.transform(p) for _, p in valid]
                xs = np.array([p.x() for p in tp], dtype=np.float64)
                ys = np.array([p.y() for p in tp], dtype=np.float64)
            try:
                cache.ensure_coverage(xs, ys)
                elev = cache.sample(xs, ys, interp)
            except SamplerError as exc:
                raise QgsProcessingException(str(exc))

            for (d, p), z in zip(valid, elev):
                out = QgsFeature(fields)
                out.setGeometry(QgsGeometry.fromPointXY(p))
                z_val = NULL if (z is None or math.isnan(float(z))) \
                    else float(z)
                out.setAttributes([sec_id, float(d), z_val])
                sink.addFeature(out, QgsFeatureSink.Flag.FastInsert)
            feedback.setProgress(int(100.0 * (fi + 1) / total))

        return {self.OUTPUT: dest_id}
