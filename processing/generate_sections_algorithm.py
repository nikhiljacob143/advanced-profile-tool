# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Processing algorithm: generate cross-section lines along an alignment.

Wraps core.alignment_engine (feature → AlignmentDef resolution, multipart
join) and core.section_engine (chainage series + section construction).
The source CRS is used as the calculation CRS; geographic CRSs are
rejected because chainages and offsets must be in linear units.
"""
from qgis.core import (QgsFeature, QgsFeatureSink, QgsField, QgsFields,
                       QgsGeometry, QgsPointXY, QgsProcessingAlgorithm,
                       QgsProcessingException,
                       QgsProcessingParameterBoolean,
                       QgsProcessingParameterEnum,
                       QgsProcessingParameterFeatureSink,
                       QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterNumber, QgsProcessing,
                       QgsWkbTypes)
from qgis.PyQt.QtCore import QCoreApplication, QVariant

from ..constants import (GEOGRAPHIC_CRS_WARNING, INCLUDE_BOTH, INCLUDE_NONE,
                         MODE_INTERVAL, MULTIPART_JOIN_TOL, TANGENT_METHODS)
from ..core.alignment_engine import AlignmentError, resolve_alignments
from ..core.section_engine import build_sections, generate_chainages


class _SourceAdapter:
    """Minimal layer-like wrapper so resolve_alignments can consume a
    QgsProcessingFeatureSource."""

    def __init__(self, source):
        self._source = source

    def crs(self):
        return self._source.sourceCrs()

    def id(self):
        return "processing_source"


class GenerateSectionsAlgorithm(QgsProcessingAlgorithm):
    """Generate perpendicular cross-section lines at a regular interval."""

    INPUT = "INPUT"
    INTERVAL = "INTERVAL"
    LEFT_WIDTH = "LEFT_WIDTH"
    RIGHT_WIDTH = "RIGHT_WIDTH"
    INCLUDE_ENDPOINTS = "INCLUDE_ENDPOINTS"
    TANGENT_METHOD = "TANGENT_METHOD"
    START_CHAINAGE = "START_CHAINAGE"
    OUTPUT = "OUTPUT"

    def tr(self, text):
        return QCoreApplication.translate("GenerateSectionsAlgorithm", text)

    def createInstance(self):
        return GenerateSectionsAlgorithm()

    def name(self):
        return "generatesections"

    def displayName(self):
        return self.tr("Generate cross sections")

    def group(self):
        return self.tr("Profiles")

    def groupId(self):
        return "profiles"

    def shortHelpString(self):
        return self.helpString()

    def helpString(self):
        return self.tr(
            "<p>Generates cross-section lines perpendicular to an alignment "
            "at a regular chainage interval. Each output line has three "
            "vertices (left end, alignment crossing, right end) so the "
            "alignment position is preserved in the geometry. Attributes "
            "record the section id, label, chainage and whether the section "
            "is a major section. Multipart alignments are joined "
            "end-to-end where the parts touch within a small tolerance. "
            "The layer CRS is used for all distance calculations, so the "
            "input must be in a projected CRS — reproject geographic "
            "(degree-based) layers first. Offsets follow the plugin "
            "convention: negative to the left of the direction of "
            "increasing chainage.</p>")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Alignment layer"),
            [QgsProcessing.SourceType.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterNumber(
            self.INTERVAL, self.tr("Section interval"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=20.0,
            minValue=0.001))
        self.addParameter(QgsProcessingParameterNumber(
            self.LEFT_WIDTH, self.tr("Left width"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=25.0,
            minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.RIGHT_WIDTH, self.tr("Right width"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=25.0,
            minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.INCLUDE_ENDPOINTS, self.tr("Include alignment endpoints"),
            defaultValue=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.TANGENT_METHOD, self.tr("Tangent method"),
            options=[self.tr("Local segment"), self.tr("Averaged"),
                     self.tr("Smoothed")],
            defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.START_CHAINAGE, self.tr("Start chainage offset"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=0.0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Cross sections"),
            QgsProcessing.SourceType.TypeVectorLine))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(
                self.tr("Invalid alignment layer."))
        interval = self.parameterAsDouble(parameters, self.INTERVAL, context)
        left_w = self.parameterAsDouble(parameters, self.LEFT_WIDTH, context)
        right_w = self.parameterAsDouble(parameters, self.RIGHT_WIDTH,
                                         context)
        include_ends = self.parameterAsBoolean(parameters,
                                               self.INCLUDE_ENDPOINTS,
                                               context)
        method_idx = self.parameterAsEnum(parameters, self.TANGENT_METHOD,
                                          context)
        tangent_method = TANGENT_METHODS[method_idx]
        start_ch = self.parameterAsDouble(parameters, self.START_CHAINAGE,
                                          context)

        crs = source.sourceCrs()
        if crs.isValid() and crs.isGeographic():
            raise QgsProcessingException(self.tr(GEOGRAPHIC_CRS_WARNING))

        fields = QgsFields()
        fields.append(QgsField("section_id", QVariant.Int))
        fields.append(QgsField("label", QVariant.String))
        fields.append(QgsField("chainage", QVariant.Double))
        fields.append(QgsField("is_major", QVariant.Int))
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.Type.LineString, crs)
        if sink is None:
            raise QgsProcessingException(
                self.tr("Could not create the output sink."))

        try:
            alignments, warnings = resolve_alignments(
                _SourceAdapter(source), list(source.getFeatures()),
                calc_crs=crs, multipart_mode=MULTIPART_JOIN_TOL,
                start_chainage=start_ch)
        except AlignmentError as exc:
            raise QgsProcessingException(str(exc))
        for w in warnings:
            feedback.pushWarning(w)

        include = INCLUDE_BOTH if include_ends else INCLUDE_NONE
        settings = {
            "left_width": left_w,
            "right_width": right_w,
            "tangent_method": tangent_method,
        }
        total = max(len(alignments), 1)
        next_id = 1
        for ai, alignment in enumerate(alignments):
            if feedback.isCanceled():
                break
            chainages, info, wns = generate_chainages(
                alignment, MODE_INTERVAL, alignment.start_chainage,
                alignment.end_chainage, interval=interval, include=include)
            for w in wns:
                feedback.pushWarning(w)
            if "error" in info:
                feedback.pushWarning(
                    f"{alignment.name}: {info['error']} — skipped.")
                continue
            settings["section_start_number"] = next_id
            sections, wns = build_sections(alignment, chainages, settings)
            for w in wns:
                feedback.pushWarning(w)
            for sec in sections:
                if feedback.isCanceled():
                    break
                feat = QgsFeature(fields)
                lp, r = sec.left_point, sec.right_point
                feat.setGeometry(QgsGeometry.fromPolylineXY([
                    QgsPointXY(lp[0], lp[1]),
                    QgsPointXY(sec.center[0], sec.center[1]),
                    QgsPointXY(r[0], r[1])]))
                feat.setAttributes([sec.section_id, sec.label,
                                    float(sec.chainage),
                                    1 if sec.is_major else 0])
                sink.addFeature(feat, QgsFeatureSink.Flag.FastInsert)
            next_id += len(sections)
            feedback.setProgress(int(100.0 * (ai + 1) / total))

        return {self.OUTPUT: dest_id}
