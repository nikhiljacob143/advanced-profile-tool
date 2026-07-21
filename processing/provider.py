# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""QGIS Processing provider for the Advanced Profile Tool.

Registers the three headless algorithms (generate sections, extract
profiles, compare surfaces) under the provider id ``advancedprofile`` so
they are usable from the Processing Toolbox, the model designer and
batch/scripted workflows.
"""
import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .compare_surfaces_algorithm import CompareSurfacesAlgorithm
from .extract_profiles_algorithm import ExtractProfilesAlgorithm
from .generate_sections_algorithm import GenerateSectionsAlgorithm

_ICON_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "icons", "advanced_profile_tool.png"))


class AdvancedProfileProvider(QgsProcessingProvider):
    """Processing provider exposing the Advanced Profile Tool algorithms."""

    def id(self):
        return "advancedprofile"

    def name(self):
        return "Advanced Profile Tool"

    def longName(self):
        return self.name()

    def icon(self):
        if os.path.exists(_ICON_PATH):
            return QIcon(_ICON_PATH)
        return super().icon()

    def loadAlgorithms(self):
        self.addAlgorithm(GenerateSectionsAlgorithm())
        self.addAlgorithm(ExtractProfilesAlgorithm())
        self.addAlgorithm(CompareSurfacesAlgorithm())
