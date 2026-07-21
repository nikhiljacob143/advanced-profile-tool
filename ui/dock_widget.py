# -*- coding: utf-8 -*-
"""Main dock panel: 10 tabs orchestrating the full workflow.

Copyright (C) 2026 Nikhil Jacob — GPL v2 or later.
"""
import logging
import functools
import json
import os

import numpy as np

from qgis.PyQt.QtCore import Qt, QTimer, QUrl
from qgis.PyQt.QtGui import QKeySequence, QIcon, QDesktopServices
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QShortcut, QSpinBox, QTableView, QTableWidget,
    QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget)
from qgis.core import (Qgis, QgsApplication, QgsCoordinateReferenceSystem,
                       QgsFeatureRequest, QgsMapLayerProxyModel,
                       QgsMessageLog, QgsProject, QgsRasterLayer)
from qgis.gui import (QgsCollapsibleGroupBox, QgsColorButton, QgsDockWidget,
                      QgsFileWidget, QgsMapLayerComboBox,
                      QgsProjectionSelectionWidget)

from ..constants import (CHAINAGE_FORMATS, DEFAULTS, INCLUDE_BOTH,
                         INCLUDE_END, INCLUDE_NONE, INCLUDE_START,
                         INTERP_METHODS, LINE_Z_SURFACE_ID, LOG_TAG,
                         MODE_COUNT, MODE_INTERVAL,
                         MODE_LIST, MULTIPART_JOIN_TOL, MULTIPART_REJECT,
                         MULTIPART_SEPARATE, NODATA_GAP, NODATA_INTERPOLATE,
                         PLUGIN_NAME, PROJECT_SCOPE, SETTINGS_GROUP,
                         TANGENT_METHODS)
from ..core import geometry_math as gm
from ..core.alignment_engine import (AlignmentError, clamp_chainage_window,
                                     resolve_alignments)
from ..core.cache import ProfileCache
from ..core.comparison_engine import (compare_section, difference,
                                      threshold_exceedances)
from ..core.data_models import DemDef
from ..core.raster_sampler import (layer_pixel_size,
                                   suggest_sampling_interval)
from ..core.section_engine import build_sections, generate_chainages
from ..core.units import format_chainage, parse_chainage
from ..core.validation_engine import (check_dem_consistency,
                                      check_dem_coverage, check_parameters)
from ..core.volume_engine import average_end_area
from ..export.csv_exporter import (export_comparison, export_cross_sections,
                                   export_long_section, export_volumes)
from ..export.output_manager import OutputManager
from ..export.report_templates import default_title_block
from ..map_tools.alignment_picker import AlignmentPickerTool
from ..map_tools.preview_overlay import SectionPreviewOverlay
from ..map_tools.section_add_tool import SectionAddTool
from ..persistence.presets import PresetStore
from ..persistence.recent_projects import RecentList
from ..plotting.plot_controller import PlotController
from ..plotting.profile_plot import ProfilePlotWidget
from ..settings import SettingsManager
from ..styling.label_manager import apply_section_labels
from ..styling.section_renderer import apply_section_style
from ..tasks.export_task import ExportTask
from ..tasks.generation_task import GenerateAndSampleTask
from ..ui.models import DemTableModel
from ..ui.validators import ChainageValidator, chainage_from_edit

INCLUDE_OPTIONS = [("Include both ends", INCLUDE_BOTH),
                   ("Include start only", INCLUDE_START),
                   ("Include end only", INCLUDE_END),
                   ("Exclude both ends", INCLUDE_NONE)]
MULTIPART_OPTIONS = [("Join touching parts", MULTIPART_JOIN_TOL),
                     ("Treat parts separately", MULTIPART_SEPARATE),
                     ("Reject multipart", MULTIPART_REJECT)]


def _scroll(widget):
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QScrollArea.Shape.NoFrame)
    sa.setWidget(widget)
    return sa


_LOG = logging.getLogger(__name__)

class AdvancedProfileDock(QgsDockWidget):
    """The Advanced Profile Tool panel."""

    def __init__(self, iface, parent=None):
        super().__init__(PLUGIN_NAME, parent)
        self.setObjectName("AdvancedProfileToolDock")
        self.iface = iface
        self.settings = SettingsManager()
        self.presets = PresetStore()
        self.overlay = SectionPreviewOverlay(iface.mapCanvas())
        self.dem_model = DemTableModel([])
        self.alignments = []
        self.alignment = None
        self.sections = []
        self.profiles = []
        self.long_profile = None
        self.comparisons = []
        self.volume_rows = []
        self.volume_totals = {}
        self._task = None
        self._export_task = None
        self._align_queue = []
        self._batch_export = False
        self._batch_total = 0
        self._map_tool = None
        self._out_mgr = None
        self._profile_cache = ProfileCache()
        self._img_cancel = False
        self._last_sampling_s = None
        self._last_export_s = None
        self._last_run_dir = ""
        self._recent_runs = RecentList(
            key=f"{SETTINGS_GROUP}/recent_run_dirs", maxlen=10)
        self._build_ui()
        self._wire()
        self._apply_accessibility()
        self._refresh_layers()
        # ---- persistence: last-used settings, then project overrides ----
        try:
            last = self.presets.load_last()
            if isinstance(last, dict) and last:
                self.apply_settings(last)
                self._log("Last-used settings restored.", Qgis.MessageLevel.Info)
        except Exception as e:                        # noqa: BLE001
            self._log(f"Last-used settings not restored: {e}",
                      Qgis.MessageLevel.Warning)
        try:
            self._apply_project_settings()
        except Exception as e:                        # noqa: BLE001
            self._log(f"Project settings not restored: {e}", Qgis.MessageLevel.Warning)
        try:
            QgsProject.instance().readProject.connect(self._on_project_read)
        except Exception:                             # noqa: BLE001
            _LOG.debug("readProject signal not connected", exc_info=True)
        self._log("Panel ready. Set an alignment and add DEM surfaces to "
                  "begin.", Qgis.MessageLevel.Info)

    # ================================================================== #
    # UI construction
    # ================================================================== #
    def _build_ui(self):
        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(4, 4, 4, 4)

        # ---- action strip (two rows) --------------------------------------
        strip1 = QHBoxLayout()
        strip2 = QHBoxLayout()
        self.btn_preview = QPushButton("Preview")
        self.btn_sections_only = QPushButton("Generate sections")
        self.btn_extract = QPushButton("Extract profiles")
        self.btn_generate = QPushButton("Generate")
        self.btn_generate_all = QPushButton("Generate + Export")
        self.btn_export = QPushButton("Export")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_reset = QPushButton("Reset")
        self.btn_reset_settings = QPushButton("Reset settings")
        self.btn_cancel.setEnabled(False)
        for b, tip in (
                (self.btn_preview,
                 "Draw the section layout on the map without sampling"),
                (self.btn_sections_only,
                 "Build the section lines, preview them and add a "
                 "sections layer to the map — no DEM sampling"),
                (self.btn_extract,
                 "Sample the enabled DEMs along the current sections; "
                 "run 'Generate sections' (or 'Generate') first"),
                (self.btn_generate,
                 "Generate sections and sample all enabled DEMs"),
                (self.btn_generate_all,
                 "Generate, sample and export in one run"),
                (self.btn_export, "Export the current results"),
                (self.btn_cancel, "Cancel the running operation"),
                (self.btn_reset, "Clear results and the preview"),
                (self.btn_reset_settings,
                 "Restore every setting to its factory default")):
            b.setToolTip(tip)
        for b in (self.btn_preview, self.btn_sections_only,
                  self.btn_extract, self.btn_generate):
            strip1.addWidget(b)
        for b in (self.btn_generate_all, self.btn_export, self.btn_cancel,
                  self.btn_reset, self.btn_reset_settings):
            strip2.addWidget(b)
        v.addLayout(strip1)
        v.addLayout(strip2)

        info = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setMaximumHeight(14)
        self.progress.setTextVisible(False)
        self.lbl_status = QLabel("Idle")
        self.lbl_status.setMinimumWidth(160)
        info.addWidget(self.progress, 2)
        info.addWidget(self.lbl_status, 1)
        v.addLayout(info)

        # ---- persistent run summary ----------------------------------------
        sumrow = QHBoxLayout()
        self.lbl_summary = QLabel("No results yet")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setToolTip(
            "Summary of the most recent run: section count, DEM count, "
            "sampling and export times and the run folder")
        self.btn_zoom_result = QPushButton("Zoom to result")
        self.btn_zoom_result.setToolTip(
            "Zoom the map canvas to the combined extent of the "
            "generated sections")
        sumrow.addWidget(self.lbl_summary, 1)
        sumrow.addWidget(self.btn_zoom_result)
        v.addLayout(sumrow)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        v.addWidget(self.tabs, 1)
        self.setWidget(root)

        self._tab_alignment()
        self._tab_layout()
        self._tab_dems()
        self._tab_viewer()
        self._tab_compare()
        self._tab_styling()
        self._tab_labels()
        self._tab_outputs()
        self._tab_presets()
        self._tab_log()

    # ------------------------------------------------------------------ #
    def _tab_alignment(self):
        w = QWidget()
        f = QFormLayout(w)
        self.cbo_layer = QgsMapLayerComboBox()
        self.cbo_layer.setFilters(QgsMapLayerProxyModel.Filter.LineLayer)
        row = QHBoxLayout()
        row.addWidget(self.cbo_layer, 1)
        self.btn_refresh_layers = QToolButton()
        self.btn_refresh_layers.setText("⟳")
        self.btn_refresh_layers.setToolTip("Refresh layer list")
        row.addWidget(self.btn_refresh_layers)
        f.addRow("Alignment layer", row)

        self.cbo_feature_mode = QComboBox()
        self.cbo_feature_mode.addItems(
            ["Choose lines from list", "All features",
             "Current map selection"])
        self.cbo_feature_mode.setToolTip(
            "Choose lines from list: tick exactly the line(s) you want "
            "sections for — one, two or many. Each ticked line is "
            "processed as its own alignment in one run.")
        f.addRow("Features", self.cbo_feature_mode)
        self.cbo_name_field = QComboBox()
        self.cbo_name_field.setToolTip(
            "Attribute used to name alignments (optional). The name also "
            "doubles as the alignment identifier in result layer names, "
            "exports and the run manifest.")
        f.addRow("Name field", self.cbo_name_field)
        self.lst_features = QListWidget()
        self.lst_features.setMaximumHeight(110)
        self.lst_features.setToolTip(
            "Tick the line(s) to generate cross-sections for. Use the "
            "map-pick button to tick lines by clicking them on the map.")
        f.addRow("Lines", self.lst_features)
        hb_pick = QHBoxLayout()
        self.btn_pick = QPushButton("Pick lines on map")
        self.btn_pick.setToolTip(
            "Click lines on the map to tick/untick them in the list; "
            "switch to another map tool to finish")
        self.btn_feat_all = QPushButton("Tick all")
        self.btn_feat_none = QPushButton("Untick all")
        hb_pick.addWidget(self.btn_pick, 2)
        hb_pick.addWidget(self.btn_feat_all, 1)
        hb_pick.addWidget(self.btn_feat_none, 1)
        self.lbl_sel_count = QLabel("0 ticked")
        self.lbl_sel_count.setToolTip(
            "Number of lines currently ticked in the list")
        hb_pick.addWidget(self.lbl_sel_count)
        f.addRow("", hb_pick)

        self.lbl_align_info = QLabel("No alignment resolved")
        self.lbl_align_info.setWordWrap(True)
        f.addRow("Info", self.lbl_align_info)

        hb = QHBoxLayout()
        self.chk_reverse = QCheckBox("Reverse direction")
        self.btn_flash = QPushButton("Flash")
        self.btn_zoom = QPushButton("Zoom to")
        hb.addWidget(self.chk_reverse)
        hb.addWidget(self.btn_flash)
        hb.addWidget(self.btn_zoom)
        f.addRow("", hb)

        self.spn_start_ch = QDoubleSpinBox()
        self.spn_start_ch.setRange(-1e9, 1e9)
        self.spn_start_ch.setDecimals(3)
        self.spn_start_ch.setToolTip(
            "Displayed chainage at the alignment start")
        f.addRow("Start chainage offset", self.spn_start_ch)

        self.chk_full = QCheckBox("Use full alignment")
        self.chk_full.setChecked(True)
        f.addRow("", self.chk_full)
        self.edt_proc_start = QLineEdit()
        self.edt_proc_start.setValidator(ChainageValidator())
        self.edt_proc_start.setPlaceholderText("e.g. 0+000")
        self.edt_proc_end = QLineEdit()
        self.edt_proc_end.setValidator(ChainageValidator())
        self.edt_proc_end.setPlaceholderText("e.g. 1+500")
        f.addRow("Process from", self.edt_proc_start)
        f.addRow("Process to", self.edt_proc_end)

        self.cbo_chfmt = QComboBox()
        self.cbo_chfmt.addItems(CHAINAGE_FORMATS)
        f.addRow("Chainage format", self.cbo_chfmt)

        adv = QgsCollapsibleGroupBox(
            "Advanced: multipart, CRS and station equations")
        adv.setToolTip(
            "Multipart handling, calculation CRS overrides, station "
            "equations and the alignment-Z surface option")
        af = QFormLayout(adv)
        self.cbo_multipart = QComboBox()
        for label, _ in MULTIPART_OPTIONS:
            self.cbo_multipart.addItem(label)
        self.cbo_multipart.setToolTip(
            "How multipart alignment geometries are handled")
        self.spn_join_tol = QDoubleSpinBox()
        self.spn_join_tol.setRange(0.0, 1e6)
        self.spn_join_tol.setDecimals(4)
        self.spn_join_tol.setValue(DEFAULTS["multipart_join_tol"])
        self.spn_join_tol.setToolTip(
            "Maximum end-to-end gap joined when merging touching parts")
        af.addRow("Multipart handling", self.cbo_multipart)
        af.addRow("Join tolerance (m)", self.spn_join_tol)

        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setOptionVisible(
            QgsProjectionSelectionWidget.CrsOption.CrsNotSet, True)
        self.crs_widget.setToolTip(
            "Calculation CRS. Leave unset to use the layer CRS; a projected "
            "CRS is required (degree-based distances are refused).")
        row_crs = QHBoxLayout()
        row_crs.addWidget(self.crs_widget, 1)
        self.btn_crs_suggest = QPushButton("Suggest")
        self.btn_crs_suggest.setToolTip(
            "Suggest a projected CRS (UTM zone from the layer extent) "
            "when the alignment layer is stored in a geographic CRS")
        row_crs.addWidget(self.btn_crs_suggest)
        af.addRow("Calculation CRS", row_crs)
        self.chk_project_crs = QCheckBox("Use project CRS")
        self.chk_project_crs.setToolTip(
            "Set the calculation CRS to the current project CRS; untick "
            "to choose a CRS manually")
        af.addRow("", self.chk_project_crs)

        self.tbl_steq = QTableWidget(0, 2)
        self.tbl_steq.setHorizontalHeaderLabels(
            ["Raw chainage", "Ahead chainage"])
        self.tbl_steq.horizontalHeader().setStretchLastSection(True)
        self.tbl_steq.setMaximumHeight(110)
        self.tbl_steq.setToolTip(
            "Station equations: at each raw (measured) chainage the "
            "displayed chainage restarts at the ahead value. Enter "
            "values as 1+240, CH 1+240 or plain metres.")
        af.addRow("Station equations", self.tbl_steq)
        hb_eq = QHBoxLayout()
        self.btn_steq_add = QPushButton("Add row")
        self.btn_steq_add.setToolTip("Add a station equation row")
        self.btn_steq_del = QPushButton("Remove row")
        self.btn_steq_del.setToolTip(
            "Remove the selected station equation row")
        hb_eq.addWidget(self.btn_steq_add)
        hb_eq.addWidget(self.btn_steq_del)
        hb_eq.addStretch(1)
        af.addRow("", hb_eq)

        self.chk_line_z = QCheckBox("Use line Z as surface")
        self.chk_line_z.setToolTip(
            "When the alignment geometry carries Z values, plot them as "
            "an additional 'Alignment Z' surface (design grade) in the "
            "viewer and exports")
        af.addRow("", self.chk_line_z)
        adv.setCollapsed(True)
        f.addRow(adv)

        self.tabs.addTab(_scroll(w), "Alignment")

    # ------------------------------------------------------------------ #
    def _tab_layout(self):
        w = QWidget()
        f = QFormLayout(w)
        self.cbo_mode = QComboBox()
        self.cbo_mode.addItems(["By interval", "By number of sections",
                                "Chainage list"])
        self.cbo_mode.setToolTip(
            "How section chainages are generated: at a fixed interval, "
            "as an evenly spaced count, or from an explicit list")
        f.addRow("Mode", self.cbo_mode)
        self.spn_interval = QDoubleSpinBox()
        self.spn_interval.setRange(0.001, 1e9)
        self.spn_interval.setValue(DEFAULTS["section_interval"])
        self.spn_interval.setDecimals(3)
        self.spn_interval.setToolTip(
            "Chainage spacing between consecutive sections (metres)")
        f.addRow("Interval (m)", self.spn_interval)
        self.spn_count = QSpinBox()
        self.spn_count.setRange(1, 100000)
        self.spn_count.setValue(10)
        self.spn_count.setToolTip(
            "Number of evenly spaced sections to generate")
        f.addRow("Section count", self.spn_count)
        self.cbo_include = QComboBox()
        for label, _ in INCLUDE_OPTIONS:
            self.cbo_include.addItem(label)
        self.cbo_include.setToolTip(
            "Whether sections are placed at the start and end of the "
            "processed chainage window")
        f.addRow("Endpoints", self.cbo_include)

        self.txt_chainages = QPlainTextEdit()
        self.txt_chainages.setPlaceholderText(
            "One chainage per line (0+240, CH 1+200 or plain metres). "
            "Invalid values are reported and skipped.")
        self.txt_chainages.setMaximumHeight(90)
        f.addRow("Chainage list", self.txt_chainages)
        hb = QHBoxLayout()
        self.btn_load_csv = QPushButton("Load CSV/TXT/XLSX…")
        self.btn_click_add = QPushButton("Add by map click")
        self.chk_vertices = QCheckBox("Include vertices")
        hb.addWidget(self.btn_load_csv)
        hb.addWidget(self.btn_click_add)
        hb.addWidget(self.chk_vertices)
        f.addRow("", hb)
        self.chk_keep_order = QCheckBox("Keep entered order (no sorting)")
        self.chk_keep_order.setToolTip(
            "Retain the entered chainage order; duplicates are still "
            "removed. Section numbering follows the entered order.")
        f.addRow("", self.chk_keep_order)

        self.cbo_width_mode = QComboBox()
        self.cbo_width_mode.addItems(
            ["Separate left/right", "Equal both sides", "Total width"])
        self.cbo_width_mode.setToolTip(
            "Separate: independent left and right widths. Equal: one "
            "value applied to both sides. Total: one overall width "
            "split half each side of the alignment.")
        f.addRow("Width mode", self.cbo_width_mode)
        self.spn_left = QDoubleSpinBox()
        self.spn_left.setRange(0.0, 1e6)
        self.spn_left.setValue(DEFAULTS["left_width"])
        self.spn_left.setToolTip(
            "Section width to the left of the alignment (metres); also "
            "the both-sides value in 'Equal' width mode")
        self.spn_right = QDoubleSpinBox()
        self.spn_right.setRange(0.0, 1e6)
        self.spn_right.setValue(DEFAULTS["right_width"])
        self.spn_right.setToolTip(
            "Section width to the right of the alignment (metres)")
        f.addRow("Left width (m)", self.spn_left)
        f.addRow("Right width (m)", self.spn_right)
        self.spn_total = QDoubleSpinBox()
        self.spn_total.setRange(0.0, 2e6)
        self.spn_total.setValue(DEFAULTS["total_width"])
        self.spn_total.setToolTip(
            "Total section width (metres), split half each side of the "
            "alignment in 'Total width' mode")
        f.addRow("Total width (m)", self.spn_total)

        self.lbl_readback = QLabel(" ")
        self.lbl_readback.setWordWrap(True)
        self.lbl_readback.setToolTip(
            "Live read-back of the section layout for the last resolved "
            "alignment: count, first and last chainage and the leftover "
            "distance beyond the last section")
        f.addRow("Layout", self.lbl_readback)

        advs = QgsCollapsibleGroupBox("Advanced: tangent, bearing and "
                                      "naming")
        advs.setToolTip(
            "Tangent construction, fixed bearings, vertex handling and "
            "section naming")
        sf = QFormLayout(advs)
        self.cbo_tangent = QComboBox()
        self.cbo_tangent.addItems(
            ["Local segment", "Averaged", "Smoothed"])
        self.cbo_tangent.setToolTip(
            "How the alignment direction is derived at each chainage: "
            "local segment, averaged over a distance, or smoothed")
        sf.addRow("Tangent method", self.cbo_tangent)
        self.spn_tangent_d = QDoubleSpinBox()
        self.spn_tangent_d.setRange(0.1, 1e6)
        self.spn_tangent_d.setValue(DEFAULTS["tangent_avg_distance"])
        self.spn_tangent_d.setToolTip(
            "Distance over which the tangent is averaged/smoothed")
        sf.addRow("Averaging distance (m)", self.spn_tangent_d)
        self.cbo_vertex = QComboBox()
        self.cbo_vertex.addItems(
            ["Angle bisector", "Incoming segment", "Outgoing segment"])
        self.cbo_vertex.setToolTip(
            "Section direction exactly at alignment vertices: the angle "
            "bisector of the adjoining segments (recommended), or the "
            "incoming/outgoing segment direction. Applies to the local "
            "tangent method; the averaged/smoothed methods blend "
            "directions across the vertex.")
        sf.addRow("Vertex handling", self.cbo_vertex)
        self.spn_min_seg = QDoubleSpinBox()
        self.spn_min_seg.setRange(1e-6, 100.0)
        self.spn_min_seg.setDecimals(6)
        self.spn_min_seg.setValue(DEFAULTS["min_segment_length"])
        self.spn_min_seg.setToolTip(
            "Segments shorter than this are ignored when deriving the "
            "local tangent (degenerate vertex tolerance)")
        sf.addRow("Min segment length (m)", self.spn_min_seg)
        self.spn_angle = QDoubleSpinBox()
        self.spn_angle.setRange(-89.0, 89.0)
        self.spn_angle.setToolTip("Angular offset from perpendicular")
        sf.addRow("Angle offset (°)", self.spn_angle)
        self.chk_swap = QCheckBox("Reverse left/right convention")
        self.chk_swap.setToolTip(
            "Swap which side of the alignment is treated as left")
        sf.addRow("", self.chk_swap)
        hb_b = QHBoxLayout()
        self.chk_bearing = QCheckBox("Fixed bearing (°)")
        self.chk_bearing.setToolTip(
            "Draw every section on a fixed bearing (degrees clockwise "
            "from north) instead of perpendicular to the alignment — "
            "for skewed crossings and structure grids")
        self.spn_bearing = QDoubleSpinBox()
        self.spn_bearing.setRange(0.0, 360.0)
        self.spn_bearing.setDecimals(1)
        self.spn_bearing.setEnabled(False)
        self.spn_bearing.setToolTip(
            "Bearing in degrees clockwise from north")
        hb_b.addWidget(self.chk_bearing)
        hb_b.addWidget(self.spn_bearing)
        sf.addRow("", hb_b)

        self.edt_prefix = QLineEdit(DEFAULTS["section_prefix"])
        self.edt_prefix.setToolTip("Prefix for section labels, e.g. XS")
        self.spn_num0 = QSpinBox()
        self.spn_num0.setRange(0, 10_000_000)
        self.spn_num0.setValue(DEFAULTS["section_start_number"])
        self.spn_num0.setToolTip("Number assigned to the first section")
        self.spn_pad = QSpinBox()
        self.spn_pad.setRange(0, 8)
        self.spn_pad.setValue(DEFAULTS["section_number_padding"])
        self.spn_pad.setToolTip(
            "Zero-padding of section numbers, e.g. 2 → XS01")
        self.spn_major = QSpinBox()
        self.spn_major.setRange(0, 1000)
        self.spn_major.setValue(DEFAULTS["major_every"])
        self.spn_major.setToolTip("Every Nth section is 'major' (0 = none)")
        sf.addRow("Section prefix", self.edt_prefix)
        sf.addRow("Start number", self.spn_num0)
        sf.addRow("Number padding", self.spn_pad)
        sf.addRow("Major every", self.spn_major)
        advs.setCollapsed(True)
        f.addRow(advs)

        self.lbl_param_err = QLabel("")
        self.lbl_param_err.setStyleSheet("color: #C62828")
        self.lbl_param_err.setWordWrap(True)
        self.lbl_param_err.setToolTip(
            "Current parameter problems; empty when the section "
            "parameters are valid")
        f.addRow("", self.lbl_param_err)
        self.chk_autopreview = QCheckBox("Auto-preview")
        self.chk_autopreview.setToolTip(
            "Re-draw the map preview automatically (0.4 s after the "
            "last change) whenever widths, interval, mode or tangent "
            "settings change")
        f.addRow("", self.chk_autopreview)
        self.tabs.addTab(_scroll(w), "Sections")

    # ------------------------------------------------------------------ #
    def _tab_dems(self):
        w = QWidget()
        v = QVBoxLayout(w)
        hb = QHBoxLayout()
        self.btn_dem_add = QPushButton("Add from project…")
        self.btn_dem_file = QPushButton("Add from file…")
        self.btn_dem_remove = QPushButton("Remove")
        self.btn_dem_up = QToolButton()
        self.btn_dem_up.setText("▲")
        self.btn_dem_down = QToolButton()
        self.btn_dem_down.setText("▼")
        self.btn_dem_coverage = QPushButton("Check coverage")
        for b, tip in ((self.btn_dem_add,
                        "Add a raster layer already in the project"),
                       (self.btn_dem_file, "Add a raster from disk"),
                       (self.btn_dem_remove, "Remove the selected DEM"),
                       (self.btn_dem_up, "Move the selected DEM up"),
                       (self.btn_dem_down, "Move the selected DEM down"),
                       (self.btn_dem_coverage,
                        "Check that the current (or freshly built) "
                        "sections fall inside the enabled DEM extents")):
            b.setToolTip(tip)
            hb.addWidget(b)
        hb.addStretch(1)
        v.addLayout(hb)
        self.lbl_dem_warn = QLabel("")
        self.lbl_dem_warn.setWordWrap(True)
        self.lbl_dem_warn.setStyleSheet("color: #E65100")
        self.lbl_dem_warn.setToolTip(
            "Persistent DEM consistency/coverage indicator from the "
            "most recent check")
        v.addWidget(self.lbl_dem_warn)
        self.tbl_dems = QTableView()
        self.tbl_dems.setModel(self.dem_model)
        self.tbl_dems.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_dems.horizontalHeader().setStretchLastSection(True)
        # individual styling per DEM surface: colour picker, line style,
        # width, interpolation — all edited in place
        from ..ui.delegates import ColorDelegate, ComboDelegate, SpinDelegate
        from ..ui.models import (COL_BAND, COL_COLOR, COL_INTERP, COL_STYLE,
                                 COL_VOFF, COL_WIDTH, LINE_STYLES)
        self._delegates = [
            (COL_COLOR, ColorDelegate(self.tbl_dems)),
            (COL_INTERP, ComboDelegate(INTERP_METHODS, self.tbl_dems)),
            (COL_STYLE, ComboDelegate(LINE_STYLES, self.tbl_dems)),
            (COL_WIDTH, SpinDelegate(0.1, 10.0, 1, 0.2, self.tbl_dems)),
            (COL_BAND, SpinDelegate(1, 64, 0, parent=self.tbl_dems)),
            (COL_VOFF, SpinDelegate(-1000.0, 1000.0, 3, 0.1,
                                    self.tbl_dems)),
        ]
        for col, dlg in self._delegates:
            self.tbl_dems.setItemDelegateForColumn(col, dlg)
        self.tbl_dems.setToolTip(
            "Tick 'Ref' to mark the reference surface for comparisons. "
            "Double-click a cell to edit — Colour opens a colour picker; "
            "Style and Interp offer drop-down choices. Each DEM keeps its "
            "own colour, line style and width in the profile viewer and "
            "all exports.")
        v.addWidget(self.tbl_dems, 1)

        f = QFormLayout()
        self.spn_sample = QDoubleSpinBox()
        self.spn_sample.setRange(0.0, 1e6)
        self.spn_sample.setDecimals(3)
        self.spn_sample.setSpecialValueText("auto")
        self.spn_sample.setValue(0.0)
        self.spn_sample.setToolTip(
            "Profile sampling interval; 'auto' derives it from the finest "
            "DEM pixel size")
        hb2 = QHBoxLayout()
        hb2.addWidget(self.spn_sample, 1)
        self.btn_suggest = QPushButton("Suggest")
        hb2.addWidget(self.btn_suggest)
        f.addRow("Sampling interval (m)", hb2)
        self.cbo_nodata = QComboBox()
        self.cbo_nodata.addItems(
            ["Warn and leave gaps", "Interpolate short gaps"])
        self.spn_maxgap = QDoubleSpinBox()
        self.spn_maxgap.setRange(0.0, 1e6)
        self.spn_maxgap.setValue(DEFAULTS["nodata_max_gap"])
        f.addRow("NoData handling", self.cbo_nodata)
        f.addRow("Max gap to bridge (m)", self.spn_maxgap)
        v.addLayout(f)
        self.tabs.addTab(_scroll(w), "DEMs")

    # ------------------------------------------------------------------ #
    def _tab_viewer(self):
        w = QWidget()
        v = QVBoxLayout(w)
        hb = QHBoxLayout()
        self.cbo_view_mode = QComboBox()
        self.cbo_view_mode.addItems(["Cross-section", "Long section"])
        self.cbo_section = QComboBox()
        self.cbo_section.setMinimumWidth(150)
        self.btn_prev = QToolButton()
        self.btn_prev.setText("◀")
        self.btn_next = QToolButton()
        self.btn_next.setText("▶")
        hb.addWidget(self.cbo_view_mode)
        hb.addWidget(self.btn_prev)
        hb.addWidget(self.cbo_section, 1)
        hb.addWidget(self.btn_next)
        v.addLayout(hb)

        hb2 = QHBoxLayout()
        hb2.addWidget(QLabel("VE"))
        self.spn_ve = QDoubleSpinBox()
        self.spn_ve.setRange(0.1, 100.0)
        self.spn_ve.setValue(DEFAULTS["vertical_exaggeration"])
        self.spn_ve.setSingleStep(0.5)
        hb2.addWidget(self.spn_ve)
        self.btn_true = QPushButton("1:1")
        self.btn_true.setMaximumWidth(40)
        hb2.addWidget(self.btn_true)
        self.chk_datum = QCheckBox("Datum RL")
        hb2.addWidget(self.chk_datum)
        self.spn_datum = QDoubleSpinBox()
        self.spn_datum.setRange(-12000, 12000)
        self.spn_datum.setDecimals(2)
        hb2.addWidget(self.spn_datum)
        self.chk_shade = QCheckBox("Cut/fill shading")
        hb2.addWidget(self.chk_shade)
        self.chk_diff = QCheckBox("Δz profile")
        self.chk_diff.setToolTip(
            "Plot the difference (comparison − reference) between the "
            "surfaces chosen on the Compare tab instead of the surfaces")
        hb2.addWidget(self.chk_diff)
        self.btn_clear_marks = QPushButton("Clear markers")
        hb2.addWidget(self.btn_clear_marks)
        self.btn_copy = QPushButton("Copy")
        self.btn_copy.setToolTip("Copy plot to clipboard")
        hb2.addWidget(self.btn_copy)
        hb2.addStretch(1)
        v.addLayout(hb2)

        dark = self.palette().window().color().value() < 128
        self.plot = ProfilePlotWidget(dark=dark)
        v.addWidget(self.plot, 1)
        self.lbl_readout = QLabel(" ")
        self.lbl_readout.setStyleSheet("font-family: monospace")
        v.addWidget(self.lbl_readout)
        self.controller = PlotController(self.plot)
        self.tabs.addTab(w, "Viewer")

    # ------------------------------------------------------------------ #
    def _tab_compare(self):
        w = QWidget()
        v = QVBoxLayout(w)
        f = QFormLayout()
        self.cbo_ref = QComboBox()
        self.cbo_ref.setToolTip(
            "Existing / reference surface for cut-fill comparison")
        self.cbo_cmp = QComboBox()
        self.cbo_cmp.setToolTip(
            "Design / comparison surface for cut-fill comparison")
        f.addRow("Reference surface", self.cbo_ref)
        f.addRow("Comparison surface", self.cbo_cmp)
        self.cbo_convention = QComboBox()
        self.cbo_convention.addItems(
            ["Fill = comparison above reference",
             "Fill = reference above comparison"])
        self.cbo_convention.setToolTip(
            "Cut/fill sign convention. The second option swaps the "
            "surfaces when computing areas and volumes.")
        f.addRow("Convention", self.cbo_convention)
        self.spn_dz_tol = QDoubleSpinBox()
        self.spn_dz_tol.setRange(0.01, 100.0)
        self.spn_dz_tol.setDecimals(2)
        self.spn_dz_tol.setSingleStep(0.1)
        self.spn_dz_tol.setValue(0.5)
        self.spn_dz_tol.setToolTip(
            "Sections where |Δz| exceeds this tolerance are highlighted "
            "in the comparison table and summarised in the log")
        f.addRow("|Δz| tolerance (m)", self.spn_dz_tol)
        self.chk_prismoidal = QCheckBox("Prismoidal correction")
        f.addRow("", self.chk_prismoidal)
        self.btn_compute = QPushButton("Compute comparison and volumes")
        f.addRow("", self.btn_compute)
        self.btn_masshaul = QPushButton("Mass-haul diagram")
        self.btn_masshaul.setToolTip(
            "Plot cumulative net volume against chainage for the "
            "computed volume rows")
        f.addRow("", self.btn_masshaul)
        v.addLayout(f)
        self.tbl_comp = QTableWidget(0, 6)
        self.tbl_comp.setHorizontalHeaderLabels(
            ["Section", "Chainage", "Cut area", "Fill area", "Net area",
             "Gap (m)"])
        self.tbl_comp.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        v.addWidget(self.tbl_comp, 1)
        self.tbl_vol = QTableWidget(0, 8)
        self.tbl_vol.setHorizontalHeaderLabels(
            ["From", "To", "Length", "Cut vol", "Fill vol", "Net vol",
             "Cum cut", "Cum fill"])
        self.tbl_vol.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        v.addWidget(self.tbl_vol, 1)
        self.lbl_totals = QLabel("No volumes computed")
        v.addWidget(self.lbl_totals)
        self.tabs.addTab(w, "Compare")

    # ------------------------------------------------------------------ #
    def _tab_styling(self):
        w = QWidget()
        f = QFormLayout(w)
        self.col_minor = QgsColorButton()
        self.col_minor.setColor(self._qcolor(DEFAULTS["line_color"]))
        self.col_minor.setToolTip("Colour of ordinary section lines")
        self.col_major = QgsColorButton()
        self.col_major.setColor(self._qcolor(DEFAULTS["line_color_major"]))
        self.col_major.setToolTip("Colour of major section lines")
        f.addRow("Section colour", self.col_minor)
        f.addRow("Major section colour", self.col_major)
        self.spn_w_minor = QDoubleSpinBox()
        self.spn_w_minor.setRange(0.05, 10.0)
        self.spn_w_minor.setValue(DEFAULTS["line_width_mm"])
        self.spn_w_minor.setToolTip(
            "Line width of ordinary section lines (mm)")
        self.spn_w_major = QDoubleSpinBox()
        self.spn_w_major.setRange(0.05, 10.0)
        self.spn_w_major.setValue(DEFAULTS["line_width_major_mm"])
        self.spn_w_major.setToolTip(
            "Line width of major section lines (mm)")
        f.addRow("Width (mm)", self.spn_w_minor)
        f.addRow("Major width (mm)", self.spn_w_major)
        self.cbo_pen = QComboBox()
        self.cbo_pen.addItems(["solid", "dash", "dot", "dash dot"])
        self.cbo_pen.setToolTip("Pen style of the section lines")
        f.addRow("Line style", self.cbo_pen)
        self.tabs.addTab(_scroll(w), "Styling")

    def _tab_labels(self):
        w = QWidget()
        f = QFormLayout(w)
        self.edt_label_fmt = QLineEdit(DEFAULTS["label_format"])
        self.edt_label_fmt.setToolTip(
            "Placeholders: {prefix} {number} {chainage} {alignment}")
        f.addRow("Label format", self.edt_label_fmt)
        self.cbo_label_pos = QComboBox()
        self.cbo_label_pos.addItems(["left", "right", "both", "centre"])
        self.cbo_label_pos.setToolTip(
            "Where the label sits relative to the section line")
        f.addRow("Position", self.cbo_label_pos)
        self.spn_label_size = QSpinBox()
        self.spn_label_size.setRange(4, 40)
        self.spn_label_size.setValue(8)
        self.spn_label_size.setToolTip("Label font size (points)")
        f.addRow("Font size", self.spn_label_size)
        self.chk_label_major = QCheckBox("Label major sections only")
        self.chk_label_major.setToolTip(
            "Suppress labels on ordinary sections; label majors only")
        f.addRow("", self.chk_label_major)
        self.btn_apply_labels = QPushButton(
            "Apply styling + labels to existing result layers")
        self.btn_apply_labels.setToolTip(
            "Restyles and relabels every 'Sections —' layer already in "
            "the project with the current Styling and Labels settings — "
            "no regeneration needed. New runs always use the current "
            "settings automatically.")
        f.addRow("", self.btn_apply_labels)
        self.tabs.addTab(_scroll(w), "Labels")

    def _reapply_styling_to_results(self):
        """Apply the CURRENT styling + label settings to every existing
        section result layer in the project (live update — the user
        should not need to regenerate to see label/style changes)."""
        s = self.collect_settings()
        n = 0
        for lyr in QgsProject.instance().mapLayers().values():
            try:
                if lyr.name().startswith("Sections —") and \
                        "label" in lyr.fields().names():
                    apply_section_style(lyr, s)
                    apply_section_labels(lyr, s)
                    lyr.triggerRepaint()
                    n += 1
            except Exception:                          # noqa: BLE001
                _LOG.debug("Layer skipped while re-applying section style",
                           exc_info=True)
                continue
        if n:
            self._log(f"Styling and labels re-applied to {n} section "
                      "layer(s).")
            self.iface.mapCanvas().refreshAllLayers()
        else:
            self._log("No section result layers found to restyle.",
                      Qgis.MessageLevel.Warning)

    # ------------------------------------------------------------------ #
    def _tab_outputs(self):
        w = QWidget()
        f = QFormLayout(w)
        self.out_dir = QgsFileWidget()
        self.out_dir.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        self.out_dir.setToolTip(
            "Base directory for all exported files; each run creates a "
            "subfolder when timestamped runs are enabled")
        f.addRow("Output directory", self.out_dir)
        self.chk_beside = QCheckBox("Beside the QGIS project")
        self.chk_beside.setToolTip(
            "Write outputs to a 'profile_outputs' folder next to the "
            "saved project file instead of the directory above "
            "(requires a saved project)")
        f.addRow("", self.chk_beside)
        self.edt_gpkg = QLineEdit(DEFAULTS["gpkg_name"])
        self.edt_gpkg.setToolTip(
            "File name of the GeoPackage that receives the vector "
            "results")
        f.addRow("GeoPackage name", self.edt_gpkg)
        self.cbo_vecfmt = QComboBox()
        self.cbo_vecfmt.addItems(["GeoPackage (gpkg)", "Shapefile (shp)",
                                  "GeoJSON (geojson)"])
        self.cbo_vecfmt.setToolTip(
            "File format for exported vector layers (sections, profile "
            "points, difference points)")
        f.addRow("Vector format", self.cbo_vecfmt)
        self.chk_timestamp = QCheckBox("Timestamped run subfolder")
        self.chk_timestamp.setChecked(True)
        self.chk_timestamp.setToolTip(
            "Create a new date-time subfolder for every run")
        self.chk_subfolders = QCheckBox("Subfolders by output type")
        self.chk_subfolders.setChecked(True)
        self.chk_subfolders.setToolTip(
            "Sort outputs into csv/, excel/, images/, dxf/, pdf/ and "
            "gis/ subfolders")
        self.chk_memory = QCheckBox(
            "Keep result layers as temporary (memory) layers")
        self.chk_memory.setToolTip(
            "Do not write result layers to disk when generating; they "
            "remain temporary scratch layers")
        f.addRow("", self.chk_timestamp)
        f.addRow("", self.chk_subfolders)
        f.addRow("", self.chk_memory)

        grp = QGroupBox("Formats")
        g = QGridLayout(grp)
        self.chk_f_gpkg = QCheckBox("GeoPackage layers")
        self.chk_f_csv = QCheckBox("CSV tables")
        self.chk_f_xlsx = QCheckBox("Excel workbook")
        # openpyxl is optional — never imported at plugin load; probe it
        # here (dock creation) so the UI states the situation clearly.
        from ..export.excel_exporter import (OPENPYXL_AVAILABLE,
                                             OPENPYXL_MISSING_MSG)
        if not OPENPYXL_AVAILABLE:
            self.chk_f_xlsx.setChecked(False)
            self.chk_f_xlsx.setEnabled(False)
            self.chk_f_xlsx.setText("Excel workbook (openpyxl not installed)")
            self.chk_f_xlsx.setToolTip(OPENPYXL_MISSING_MSG)
        self.chk_f_png = QCheckBox("PNG plots")
        self.chk_f_svg = QCheckBox("SVG plots")
        self.chk_f_dxfg = QCheckBox("DXF (geometry)")
        self.chk_f_dxfs = QCheckBox("DXF (section sheets)")
        self.chk_f_dxfw = QCheckBox("DXF (plan view, world coords)")
        self.chk_f_dxfw.setToolTip(
            "Plan-view polylines of the sampled sections in world "
            "coordinates (splits at NoData gaps)")
        self.chk_f_pdf = QCheckBox("PDF section sheets")
        self.chk_f_layout = QCheckBox("QGIS Print Layout")
        self.chk_f_layout.setToolTip(
            "Create a print layout in this project with a plan-view map "
            "frame, the long-section profile and a title block")
        boxes = [self.chk_f_gpkg, self.chk_f_csv, self.chk_f_xlsx,
                 self.chk_f_png, self.chk_f_svg, self.chk_f_dxfg,
                 self.chk_f_dxfs, self.chk_f_dxfw, self.chk_f_pdf,
                 self.chk_f_layout]
        for i, b in enumerate(boxes):
            b.setChecked(True)
            g.addWidget(b, i // 2, i % 2)
        self.chk_f_dxfw.setChecked(False)
        f.addRow(grp)

        # ---- table / image output options --------------------------------
        self.cbo_delim = QComboBox()
        self.cbo_delim.addItems(["Comma ( , )", "Semicolon ( ; )", "Tab"])
        self.cbo_delim.setToolTip("Field delimiter for CSV exports")
        f.addRow("CSV delimiter", self.cbo_delim)
        self.spn_dec_ch = QSpinBox()
        self.spn_dec_ch.setRange(0, 6)
        self.spn_dec_ch.setValue(DEFAULTS["decimals_chainage"])
        self.spn_dec_ch.setToolTip(
            "Decimal places for chainages in tables")
        self.spn_dec_elev = QSpinBox()
        self.spn_dec_elev.setRange(0, 6)
        self.spn_dec_elev.setValue(DEFAULTS["decimals_elevation"])
        self.spn_dec_elev.setToolTip(
            "Decimal places for elevations in tables")
        self.spn_dec_off = QSpinBox()
        self.spn_dec_off.setRange(0, 6)
        self.spn_dec_off.setValue(DEFAULTS["decimals_offset"])
        self.spn_dec_off.setToolTip(
            "Decimal places for offsets in tables")
        f.addRow("Chainage decimals", self.spn_dec_ch)
        f.addRow("Elevation decimals", self.spn_dec_elev)
        f.addRow("Offset decimals", self.spn_dec_off)
        self.spn_dpi = QSpinBox()
        self.spn_dpi.setRange(50, 1200)
        self.spn_dpi.setValue(int(DEFAULTS["export_dpi"]))
        self.spn_dpi.setToolTip(
            "Resolution (dots per inch) of exported plot images")
        f.addRow("Image DPI", self.spn_dpi)
        self.chk_xl_per_section = QCheckBox(
            "Excel: one worksheet per section")
        self.chk_xl_per_section.setToolTip(
            "Also write each cross-section to its own worksheet in the "
            "Excel workbook")
        self.chk_xl_thumbs = QCheckBox("Excel: embed plot thumbnails")
        self.chk_xl_thumbs.setToolTip(
            "Embed the rendered PNG plots into the Excel workbook "
            "(requires PNG plots to be exported)")
        f.addRow("", self.chk_xl_per_section)
        f.addRow("", self.chk_xl_thumbs)
        self.spn_dxf_text = QDoubleSpinBox()
        self.spn_dxf_text.setRange(0.05, 100.0)
        self.spn_dxf_text.setDecimals(2)
        self.spn_dxf_text.setValue(DEFAULTS["dxf_text_height"])
        self.spn_dxf_text.setToolTip(
            "Annotation text height in DXF outputs (drawing units)")
        self.spn_dxf_cols = QSpinBox()
        self.spn_dxf_cols.setRange(1, 20)
        self.spn_dxf_cols.setValue(int(DEFAULTS["dxf_sheet_cols"]))
        self.spn_dxf_cols.setToolTip(
            "Number of section tiles per row on DXF section sheets")
        self.edt_dxf_prefix = QLineEdit(DEFAULTS["dxf_layer_prefix"])
        self.edt_dxf_prefix.setToolTip(
            "Prefix applied to every DXF layer name")
        f.addRow("DXF text height", self.spn_dxf_text)
        f.addRow("DXF sheet columns", self.spn_dxf_cols)
        f.addRow("DXF layer prefix", self.edt_dxf_prefix)

        tb = QgsCollapsibleGroupBox("Title block")
        tb.setToolTip("Project details printed on PDF sheets and layouts")
        tf = QFormLayout(tb)
        self.tb_fields = {}
        for key, label in (("tb_project", "Project"),
                           ("tb_client", "Client"),
                           ("tb_site", "Site"),
                           ("tb_author", "Author"),
                           ("tb_reviewer", "Reviewer"),
                           ("tb_drawing_number", "Drawing no."),
                           ("tb_revision", "Revision")):
            e = QLineEdit()
            e.setToolTip(f"{label} entry for the title block")
            self.tb_fields[key] = e
            tf.addRow(label, e)
        self.logo_widget = QgsFileWidget()
        self.logo_widget.setFilter("Images (*.png *.jpg *.jpeg *.bmp)")
        self.logo_widget.setToolTip(
            "Optional logo image for PDF sheets and layouts")
        tf.addRow("Logo", self.logo_widget)
        self.cbo_page = QComboBox()
        self.cbo_page.addItems(["A3", "A4", "A1"])
        self.cbo_page.setToolTip("Page size for PDF sheets and layouts")
        tf.addRow("Page size", self.cbo_page)
        self.cbo_per_page = QComboBox()
        self.cbo_per_page.addItems(["1 section per sheet",
                                    "4 sections per sheet"])
        self.cbo_per_page.setToolTip(
            "Number of section plots placed on each PDF sheet")
        tf.addRow("Layout", self.cbo_per_page)
        tb.setCollapsed(False)
        f.addRow(tb)
        hb = QHBoxLayout()
        self.btn_open_folder = QPushButton("Open output folder")
        self.btn_open_folder.setToolTip(
            "Open the most recent run folder in Windows Explorer")
        hb.addWidget(self.btn_open_folder)
        hb.addStretch(1)
        f.addRow("", hb)
        self.tabs.addTab(_scroll(w), "Outputs")

    # ------------------------------------------------------------------ #
    def _tab_presets(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.lst_presets = QListWidget()
        v.addWidget(self.lst_presets, 1)
        hb = QHBoxLayout()
        self.btn_p_save = QPushButton("Save…")
        self.btn_p_load = QPushButton("Load")
        self.btn_p_del = QPushButton("Delete")
        self.btn_p_imp = QPushButton("Import…")
        self.btn_p_exp = QPushButton("Export…")
        for b, tip in ((self.btn_p_save,
                        "Save the current settings as a named preset"),
                       (self.btn_p_load, "Load the selected preset"),
                       (self.btn_p_del, "Delete the selected preset"),
                       (self.btn_p_imp, "Import a preset from a JSON file"),
                       (self.btn_p_exp,
                        "Export the selected preset to a JSON file")):
            b.setToolTip(tip)
            hb.addWidget(b)
        v.addLayout(hb)
        lbl_recent = QLabel("Recent output folders (double-click to open)")
        v.addWidget(lbl_recent)
        self.lst_recent = QListWidget()
        self.lst_recent.setMaximumHeight(140)
        self.lst_recent.setToolTip(
            "The last ten run folders written by this plugin; "
            "double-click an entry to open it in Windows Explorer")
        v.addWidget(self.lst_recent)
        self._refresh_presets()
        self._refresh_recent_runs()
        self.tabs.addTab(w, "Presets")

    def _tab_log(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.lbl_warn_summary = QLabel("No warnings")
        self.lbl_warn_summary.setWordWrap(True)
        v.addWidget(self.lbl_warn_summary)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(2000)
        v.addWidget(self.txt_log, 1)
        hb = QHBoxLayout()
        btn_clear = QPushButton("Clear log")
        btn_clear.clicked.connect(self.txt_log.clear)
        hb.addWidget(btn_clear)
        hb.addStretch(1)
        v.addLayout(hb)
        self.tabs.addTab(w, "Log")

    # ================================================================== #
    # wiring
    # ================================================================== #
    def _wire(self):
        self.btn_refresh_layers.clicked.connect(self._refresh_layers)
        self.cbo_layer.layerChanged.connect(self._on_layer_changed)
        self.btn_pick.clicked.connect(self._start_pick)
        self.btn_feat_all.clicked.connect(
            lambda: [self.lst_features.item(i).setCheckState(Qt.CheckState.Checked)
                     for i in range(self.lst_features.count())])
        self.btn_feat_none.clicked.connect(
            lambda: [self.lst_features.item(i).setCheckState(Qt.CheckState.Unchecked)
                     for i in range(self.lst_features.count())])
        self.cbo_name_field.currentIndexChanged.connect(
            lambda *_: self._populate_feature_list())
        self.btn_flash.clicked.connect(self._flash_alignment)
        self.btn_zoom.clicked.connect(self._zoom_alignment)
        self.chk_full.toggled.connect(
            lambda on: (self.edt_proc_start.setEnabled(not on),
                        self.edt_proc_end.setEnabled(not on)))
        self.chk_full.toggled.emit(True)
        self.cbo_mode.currentIndexChanged.connect(self._mode_enable)
        self._mode_enable()
        self.chk_bearing.toggled.connect(self.spn_bearing.setEnabled)
        self.btn_crs_suggest.clicked.connect(self._suggest_crs)
        self.btn_load_csv.clicked.connect(self._load_chainage_file)
        self.btn_click_add.clicked.connect(self._start_click_add)
        self.btn_dem_add.clicked.connect(self._add_dem_from_project)
        self.btn_dem_file.clicked.connect(self._add_dem_from_file)
        self.btn_dem_remove.clicked.connect(self._remove_dem)
        self.btn_dem_up.clicked.connect(lambda: self._move_dem(-1))
        self.btn_dem_down.clicked.connect(lambda: self._move_dem(1))
        self.btn_suggest.clicked.connect(self._suggest_interval)
        self.dem_model.dataChanged.connect(self._sync_dem_combos)
        self.dem_model.rowsInserted.connect(self._sync_dem_combos)
        self.dem_model.rowsRemoved.connect(self._sync_dem_combos)
        # restyle the open plot immediately when a DEM's styling changes
        self.dem_model.dataChanged.connect(
            lambda *a: self.controller.refresh())

        self.btn_preview.clicked.connect(self.on_preview)
        self.btn_sections_only.clicked.connect(
            self.on_generate_sections_only)
        self.btn_extract.clicked.connect(self.on_extract)
        self.btn_generate.clicked.connect(
            functools.partial(self.on_generate, export_after=False))
        self.btn_generate_all.clicked.connect(
            functools.partial(self.on_generate, export_after=True))
        self.btn_export.clicked.connect(self.on_export)
        self.btn_cancel.clicked.connect(self.on_cancel)
        self.btn_reset.clicked.connect(self.on_reset)
        self.btn_reset_settings.clicked.connect(self.on_reset_settings)
        self.btn_zoom_result.clicked.connect(self._zoom_to_result)

        # ---- v1.2.0 wiring -------------------------------------------------
        self.lst_features.itemChanged.connect(self._update_sel_count)
        self.chk_project_crs.toggled.connect(self._use_project_crs_toggled)
        self.btn_steq_add.clicked.connect(lambda: self._steq_add_row())
        self.btn_steq_del.clicked.connect(self._steq_remove_row)
        self.cbo_width_mode.currentIndexChanged.connect(
            self._width_mode_enable)
        self._width_mode_enable()
        self.btn_dem_coverage.clicked.connect(self._check_coverage)
        self.chk_beside.toggled.connect(
            lambda on: self.out_dir.setEnabled(not on))
        self.lst_recent.itemDoubleClicked.connect(self._open_recent_run)
        # inline parameter validation (spec 4.32)
        for wdg in (self.spn_left, self.spn_right, self.spn_total,
                    self.spn_interval, self.spn_sample):
            wdg.editingFinished.connect(self._validate_params_inline)
        # live layout read-back + debounced auto-preview (6.02 / 6.37)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(400)
        self._preview_timer.timeout.connect(self.on_preview)
        for sig in (self.spn_interval.valueChanged,
                    self.spn_count.valueChanged,
                    self.spn_left.valueChanged,
                    self.spn_right.valueChanged,
                    self.spn_total.valueChanged,
                    self.cbo_mode.currentIndexChanged,
                    self.cbo_include.currentIndexChanged,
                    self.cbo_width_mode.currentIndexChanged,
                    self.cbo_tangent.currentIndexChanged,
                    self.chk_full.toggled,
                    self.edt_proc_start.editingFinished,
                    self.edt_proc_end.editingFinished):
            sig.connect(self._on_param_changed)
        # live theme follow (4.25)
        app = QgsApplication.instance()
        if app is not None and hasattr(app, "paletteChanged"):
            app.paletteChanged.connect(self._on_palette_changed)

        self.cbo_view_mode.currentIndexChanged.connect(
            lambda i: self.controller.set_mode("ls" if i == 1 else "xs"))
        self.cbo_section.currentIndexChanged.connect(self._on_section_combo)
        self.btn_prev.clicked.connect(self._nav_prev)
        self.btn_next.clicked.connect(self._nav_next)
        self.spn_ve.valueChanged.connect(self.plot.set_vertical_exaggeration)
        self.btn_true.clicked.connect(lambda: self.spn_ve.setValue(1.0))
        self.chk_datum.toggled.connect(self._datum_changed)
        self.spn_datum.valueChanged.connect(self._datum_changed)
        self.chk_shade.toggled.connect(self._shade_changed)
        self.chk_diff.toggled.connect(self._diff_changed)
        self.btn_clear_marks.clicked.connect(self.plot.clear_markers)
        self.btn_copy.clicked.connect(self.plot.copy_to_clipboard)
        self.plot.hover_moved.connect(self._on_plot_hover)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self._nav_prev)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self._nav_next)

        self.btn_compute.clicked.connect(self.compute_comparisons)
        self.btn_masshaul.clicked.connect(self._show_mass_haul)
        # live styling/labels: any change on the Styling or Labels tabs
        # restyles existing result layers immediately (plus the explicit
        # button for reassurance)
        self.btn_apply_labels.clicked.connect(
            self._reapply_styling_to_results)
        self.edt_label_fmt.editingFinished.connect(
            self._reapply_styling_to_results)
        self.cbo_label_pos.currentIndexChanged.connect(
            lambda *_: self._reapply_styling_to_results())
        self.spn_label_size.valueChanged.connect(
            lambda *_: self._reapply_styling_to_results())
        self.chk_label_major.toggled.connect(
            lambda *_: self._reapply_styling_to_results())
        self.cbo_pen.currentIndexChanged.connect(
            lambda *_: self._reapply_styling_to_results())
        self.col_minor.colorChanged.connect(
            lambda *_: self._reapply_styling_to_results())
        self.col_major.colorChanged.connect(
            lambda *_: self._reapply_styling_to_results())
        self.spn_w_minor.valueChanged.connect(
            lambda *_: self._reapply_styling_to_results())
        self.spn_w_major.valueChanged.connect(
            lambda *_: self._reapply_styling_to_results())
        self.btn_open_folder.clicked.connect(self._open_output_folder)
        self.btn_p_save.clicked.connect(self._preset_save)
        self.btn_p_load.clicked.connect(self._preset_load)
        self.btn_p_del.clicked.connect(self._preset_delete)
        self.btn_p_imp.clicked.connect(self._preset_import)
        self.btn_p_exp.clicked.connect(self._preset_export)

        self.plot.set_vertical_exaggeration(self.spn_ve.value())
        # map→plot cursor linkage (throttled): hovering the map canvas
        # shows the corresponding chainage on the long-section plot
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setSingleShot(True)
        self._cursor_timer.setInterval(150)
        self._cursor_timer.timeout.connect(self._update_plot_cursor)
        self._last_canvas_pt = None
        self.iface.mapCanvas().xyCoordinates.connect(self._on_canvas_move)

    def _on_canvas_move(self, point):
        self._last_canvas_pt = point
        if not self._cursor_timer.isActive():
            self._cursor_timer.start()

    def _update_plot_cursor(self):
        """Show the hovered map position as a chainage cursor on the
        long-section plot (only when it is displayed)."""
        try:
            if (self.controller.mode != "ls" or self.long_profile is None
                    or self.alignment is None
                    or self._last_canvas_pt is None):
                self.plot.show_cursor(None)
                return
            from qgis.core import (QgsCoordinateTransform, QgsGeometry,
                                   QgsPointXY)
            pt = QgsPointXY(self._last_canvas_pt)
            canvas_crs = self.iface.mapCanvas().mapSettings()\
                .destinationCrs()
            calc = QgsCoordinateReferenceSystem(self.alignment.crs_authid)
            if canvas_crs.isValid() and calc.isValid() \
                    and canvas_crs != calc:
                pt = QgsCoordinateTransform(
                    canvas_crs, calc, QgsProject.instance()).transform(pt)
            geom = getattr(self, "_align_geom", None)
            if geom is None or getattr(self, "_align_geom_id", None) \
                    != id(self.alignment):
                pts = [QgsPointXY(x, y) for x, y in self.alignment.vertices]
                geom = QgsGeometry.fromPolylineXY(pts)
                self._align_geom = geom
                self._align_geom_id = id(self.alignment)
            probe = QgsGeometry.fromPointXY(pt)
            # only react within 30 map-pixels of the alignment
            tol = self.iface.mapCanvas().mapUnitsPerPixel() * 30
            if geom.distance(probe) > tol:
                self.plot.show_cursor(None)
                return
            d = geom.lineLocatePoint(probe)
            if d < 0:
                self.plot.show_cursor(None)
                return
            self.plot.show_cursor(d + self.alignment.start_chainage)
        except Exception:                              # noqa: BLE001
            self.plot.show_cursor(None)

    # ================================================================== #
    # helpers
    # ================================================================== #
    @staticmethod
    def _qcolor(hexstr):
        from qgis.PyQt.QtGui import QColor
        return QColor(hexstr)

    def _log(self, msg, level=Qgis.MessageLevel.Info):
        QgsMessageLog.logMessage(msg, LOG_TAG, level)
        prefix = {Qgis.MessageLevel.Info: "INFO", Qgis.MessageLevel.Warning: "WARN",
                  Qgis.MessageLevel.Critical: "ERROR"}.get(level, "INFO")
        if hasattr(self, "txt_log"):           # log tab built last
            self.txt_log.appendPlainText(f"[{prefix}] {msg}")

    def _warns(self, warnings):
        for wmsg in warnings:
            self._log(wmsg, Qgis.MessageLevel.Warning)
        if warnings:
            self.lbl_warn_summary.setText(
                f"{len(warnings)} warning(s) — see the log below.")

    def _status(self, text):
        self.lbl_status.setText(text)

    def _busy(self, on):
        for b in (self.btn_preview, self.btn_generate,
                  self.btn_generate_all, self.btn_export,
                  self.btn_sections_only, self.btn_extract,
                  self.btn_reset_settings):
            b.setEnabled(not on)
        self.btn_cancel.setEnabled(on)
        if not on:
            self.progress.setValue(0)

    # ------------------------------------------------------------------ #
    def _on_palette_changed(self, *_):
        """Follow QGIS light/dark theme changes live (spec 4.25)."""
        try:
            dark = self.palette().window().color().value() < 128
            self.plot.set_theme(dark)
        except Exception:                             # noqa: BLE001
            _LOG.debug("Theme change not applied to plot", exc_info=True)

    def _update_summary(self):
        """Refresh the persistent run summary label (spec 4.20/4.21)."""
        if not self.profiles and not self.sections:
            self.lbl_summary.setText("No results yet")
            return
        n = len(self.profiles) or len(self.sections)
        m = len(self.dem_model.enabled_dems())
        parts = [f"{n} sections", f"{m} DEMs"]
        if self._last_sampling_s is not None:
            parts.append(f"sampling {self._last_sampling_s:.1f} s")
        if self._last_export_s is not None:
            parts.append(f"export {self._last_export_s:.1f} s")
        if self._last_run_dir:
            parts.append(f"run folder {self._last_run_dir}")
        self.lbl_summary.setText(" | ".join(parts))

    def _zoom_to_result(self):
        """Zoom the canvas to the extent of the current sections
        (spec 4.22)."""
        if not self.sections or self.alignment is None:
            self._log("No sections to zoom to — generate sections "
                      "first.", Qgis.MessageLevel.Warning)
            return
        from qgis.core import QgsCoordinateTransform, QgsRectangle
        xs, ys = [], []
        for sec in self.sections:
            for p in (sec.left_point, sec.right_point, sec.center):
                xs.append(p[0])
                ys.append(p[1])
        if not xs:
            return
        rect = QgsRectangle(min(xs), min(ys), max(xs), max(ys))
        calc = QgsCoordinateReferenceSystem(self.alignment.crs_authid)
        canvas_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        if calc.isValid() and canvas_crs.isValid() and calc != canvas_crs:
            try:
                rect = QgsCoordinateTransform(
                    calc, canvas_crs,
                    QgsProject.instance()).transformBoundingBox(rect)
            except Exception:                         # noqa: BLE001
                _LOG.debug("Zoom extent transform failed; using original "
                           "extent", exc_info=True)
        rect.scale(1.15)
        self.iface.mapCanvas().setExtent(rect)
        self.iface.mapCanvas().refresh()

    def _apply_accessibility(self):
        """Accessible names, tab order and remaining tooltips
        (spec 4.26-4.29)."""
        names = {
            self.btn_preview: "Preview sections",
            self.btn_sections_only: "Generate sections only",
            self.btn_extract: "Extract profiles",
            self.btn_generate: "Generate and sample",
            self.btn_generate_all: "Generate and export",
            self.btn_export: "Export results",
            self.btn_cancel: "Cancel run",
            self.btn_reset: "Reset results",
            self.btn_reset_settings: "Reset settings",
            self.btn_zoom_result: "Zoom to result",
            self.lbl_summary: "Run summary",
            self.cbo_layer: "Alignment layer",
            self.btn_refresh_layers: "Refresh layers",
            self.cbo_feature_mode: "Feature mode",
            self.cbo_name_field: "Name field",
            self.lst_features: "Line list",
            self.btn_pick: "Pick lines on map",
            self.btn_feat_all: "Tick all lines",
            self.btn_feat_none: "Untick all lines",
            self.chk_reverse: "Reverse direction",
            self.btn_flash: "Flash alignment",
            self.btn_zoom: "Zoom to alignment",
            self.spn_start_ch: "Start chainage offset",
            self.chk_full: "Use full alignment",
            self.edt_proc_start: "Process from chainage",
            self.edt_proc_end: "Process to chainage",
            self.cbo_chfmt: "Chainage format",
            self.cbo_multipart: "Multipart handling",
            self.spn_join_tol: "Join tolerance",
            self.crs_widget: "Calculation CRS",
            self.btn_crs_suggest: "Suggest CRS",
            self.chk_project_crs: "Use project CRS",
            self.tbl_steq: "Station equations table",
            self.btn_steq_add: "Add station equation",
            self.btn_steq_del: "Remove station equation",
            self.chk_line_z: "Use line Z as surface",
            self.cbo_mode: "Section mode",
            self.spn_interval: "Section interval",
            self.spn_count: "Section count",
            self.cbo_include: "Endpoint inclusion",
            self.txt_chainages: "Chainage list",
            self.btn_load_csv: "Load chainage file",
            self.btn_click_add: "Add chainage by map click",
            self.chk_vertices: "Include vertices",
            self.chk_keep_order: "Keep entered order",
            self.cbo_width_mode: "Width mode",
            self.spn_left: "Left width",
            self.spn_right: "Right width",
            self.spn_total: "Total width",
            self.cbo_tangent: "Tangent method",
            self.spn_tangent_d: "Averaging distance",
            self.cbo_vertex: "Vertex handling",
            self.spn_min_seg: "Minimum segment length",
            self.spn_angle: "Angle offset",
            self.chk_swap: "Reverse left right",
            self.chk_bearing: "Fixed bearing toggle",
            self.spn_bearing: "Fixed bearing",
            self.edt_prefix: "Section prefix",
            self.spn_num0: "Start number",
            self.spn_pad: "Number padding",
            self.spn_major: "Major section frequency",
            self.chk_autopreview: "Auto preview",
            self.btn_dem_add: "Add DEM from project",
            self.btn_dem_file: "Add DEM from file",
            self.btn_dem_remove: "Remove DEM",
            self.btn_dem_up: "Move DEM up",
            self.btn_dem_down: "Move DEM down",
            self.btn_dem_coverage: "Check DEM coverage",
            self.tbl_dems: "DEM table",
            self.spn_sample: "Sampling interval",
            self.btn_suggest: "Suggest sampling interval",
            self.cbo_nodata: "NoData handling",
            self.spn_maxgap: "Maximum gap to bridge",
            self.cbo_view_mode: "View mode",
            self.cbo_section: "Section selector",
            self.btn_prev: "Previous section",
            self.btn_next: "Next section",
            self.spn_ve: "Vertical exaggeration",
            self.btn_true: "True scale",
            self.chk_datum: "Datum toggle",
            self.spn_datum: "Datum level",
            self.chk_shade: "Cut fill shading",
            self.chk_diff: "Difference profile",
            self.btn_clear_marks: "Clear markers",
            self.btn_copy: "Copy plot",
            self.cbo_ref: "Reference surface",
            self.cbo_cmp: "Comparison surface",
            self.cbo_convention: "Cut fill convention",
            self.spn_dz_tol: "Delta z tolerance",
            self.chk_prismoidal: "Prismoidal correction",
            self.btn_compute: "Compute comparison",
            self.btn_masshaul: "Mass haul diagram",
            self.col_minor: "Section colour",
            self.col_major: "Major section colour",
            self.spn_w_minor: "Section line width",
            self.spn_w_major: "Major line width",
            self.cbo_pen: "Section line style",
            self.edt_label_fmt: "Label format",
            self.cbo_label_pos: "Label position",
            self.spn_label_size: "Label font size",
            self.chk_label_major: "Label majors only",
            self.out_dir: "Output directory",
            self.chk_beside: "Output beside project",
            self.edt_gpkg: "GeoPackage name",
            self.cbo_vecfmt: "Vector format",
            self.chk_timestamp: "Timestamped runs",
            self.chk_subfolders: "Output subfolders",
            self.chk_memory: "Keep memory layers",
            self.cbo_delim: "CSV delimiter",
            self.spn_dec_ch: "Chainage decimals",
            self.spn_dec_elev: "Elevation decimals",
            self.spn_dec_off: "Offset decimals",
            self.spn_dpi: "Image DPI",
            self.chk_xl_per_section: "Excel per section sheets",
            self.chk_xl_thumbs: "Excel thumbnails",
            self.spn_dxf_text: "DXF text height",
            self.spn_dxf_cols: "DXF sheet columns",
            self.edt_dxf_prefix: "DXF layer prefix",
            self.logo_widget: "Logo file",
            self.cbo_page: "Page size",
            self.cbo_per_page: "Sections per sheet",
            self.btn_open_folder: "Open output folder",
            self.lst_presets: "Preset list",
            self.btn_p_save: "Save preset",
            self.btn_p_load: "Load preset",
            self.btn_p_del: "Delete preset",
            self.btn_p_imp: "Import preset",
            self.btn_p_exp: "Export preset",
            self.lst_recent: "Recent output folders",
            self.txt_log: "Message log",
        }
        for wdg, name in names.items():
            try:
                wdg.setAccessibleName(name)
            except Exception:                         # noqa: BLE001
                _LOG.debug("Accessible name not set for %s", name,
                           exc_info=True)
        # explicit keyboard tab order per tab for the main flow controls
        chains = [
            (self.cbo_layer, self.cbo_feature_mode, self.cbo_name_field,
             self.lst_features, self.btn_pick, self.chk_reverse,
             self.spn_start_ch, self.chk_full, self.edt_proc_start,
             self.edt_proc_end, self.cbo_chfmt),
            (self.cbo_mode, self.spn_interval, self.spn_count,
             self.cbo_include, self.cbo_width_mode, self.spn_left,
             self.spn_right, self.spn_total, self.chk_autopreview),
            (self.btn_dem_add, self.btn_dem_file, self.btn_dem_remove,
             self.tbl_dems, self.spn_sample, self.cbo_nodata,
             self.spn_maxgap),
            (self.cbo_view_mode, self.btn_prev, self.cbo_section,
             self.btn_next, self.spn_ve),
            (self.cbo_ref, self.cbo_cmp, self.cbo_convention,
             self.spn_dz_tol, self.btn_compute),
            (self.out_dir, self.chk_beside, self.edt_gpkg,
             self.cbo_vecfmt, self.chk_timestamp, self.chk_subfolders,
             self.chk_memory),
        ]
        for chain in chains:
            for a, b in zip(chain, chain[1:]):
                try:
                    self.setTabOrder(a, b)
                except Exception:                     # noqa: BLE001
                    _LOG.debug("Tab order pair skipped", exc_info=True)

    # ------------------------------------------------------------------ #
    def _refresh_layers(self):
        # QgsMapLayerComboBox auto-tracks the project; refresh name fields
        self._on_layer_changed(self.cbo_layer.currentLayer())

    def _on_layer_changed(self, layer):
        self.cbo_name_field.blockSignals(True)
        self.cbo_name_field.clear()
        self.cbo_name_field.addItem("(feature id)")
        if layer is not None:
            for fld in layer.fields():
                self.cbo_name_field.addItem(fld.name())
            # prefer a field literally called 'name' when present
            i = self.cbo_name_field.findText(
                "name", Qt.MatchFlag.MatchFixedString)   # case-insensitive ("Name")
            if i > 0:
                self.cbo_name_field.setCurrentIndex(i)
        self.cbo_name_field.blockSignals(False)
        self._populate_feature_list()

    def _populate_feature_list(self):
        """Fill the line checklist from the current alignment layer."""
        from qgis.PyQt.QtWidgets import QListWidgetItem
        self.lst_features.clear()
        layer = self.cbo_layer.currentLayer()
        if layer is None:
            return
        name_field = None
        if self.cbo_name_field.currentIndex() > 0:
            name_field = self.cbo_name_field.currentText()
        count = 0
        for feat in layer.getFeatures():
            if count >= 500:
                self._log("Feature list truncated at 500 lines — use "
                          "'All features' or a map selection for larger "
                          "layers.", Qgis.MessageLevel.Warning)
                break
            if name_field:
                label = f"{feat[name_field]}  [id {feat.id()}]"
            else:
                label = f"Feature {feat.id()}"
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, feat.id())
            self.lst_features.addItem(item)
            count += 1
        self._update_sel_count()

    def _update_sel_count(self, *_):
        """Keep the permanent ticked-line counter current (spec 5.15)."""
        n = len(self._checked_fids())
        self.lbl_sel_count.setText(f"{n} ticked")

    def _use_project_crs_toggled(self, on):
        """Set the calculation CRS from the project CRS (spec 5.32)."""
        self.crs_widget.setEnabled(not on)
        if on:
            crs = QgsProject.instance().crs()
            if crs.isValid():
                self.crs_widget.setCrs(crs)
                self._log(f"Calculation CRS set to the project CRS "
                          f"({crs.authid()}).")
            else:
                self._log("The project CRS is not valid — set a "
                          "calculation CRS manually.", Qgis.MessageLevel.Warning)

    # ---- station equations editor (spec 5.28) ------------------------- #
    def _steq_add_row(self, raw="", ahead=""):
        r = self.tbl_steq.rowCount()
        self.tbl_steq.insertRow(r)
        self.tbl_steq.setItem(r, 0, QTableWidgetItem(str(raw)))
        self.tbl_steq.setItem(r, 1, QTableWidgetItem(str(ahead)))

    def _steq_remove_row(self):
        r = self.tbl_steq.currentRow()
        if r < 0:
            r = self.tbl_steq.rowCount() - 1
        if r >= 0:
            self.tbl_steq.removeRow(r)

    def _station_equations_from_table(self):
        """Serialise the station equations table to [[raw, ahead], ...].

        Values are parsed with core.units.parse_chainage; rows with an
        unparseable value are skipped with a warning."""
        eqs = []
        for r in range(self.tbl_steq.rowCount()):
            it0 = self.tbl_steq.item(r, 0)
            it1 = self.tbl_steq.item(r, 1)
            t0 = it0.text().strip() if it0 else ""
            t1 = it1.text().strip() if it1 else ""
            if not t0 and not t1:
                continue
            raw = parse_chainage(t0)
            ahead = parse_chainage(t1)
            if raw is None or ahead is None:
                self._log(f"Station equation row {r + 1} skipped — "
                          f"'{t0}' / '{t1}' is not a valid chainage "
                          "pair.", Qgis.MessageLevel.Warning)
                continue
            eqs.append([float(raw), float(ahead)])
        return eqs

    def _set_station_equations(self, eqs):
        """Restore the station equations table from a settings list."""
        self.tbl_steq.setRowCount(0)
        for pair in (eqs or []):
            try:
                raw, ahead = float(pair[0]), float(pair[1])
            except (TypeError, ValueError, IndexError):
                _LOG.debug("Skipping malformed station equation %r", pair)
                continue
            self._steq_add_row(f"{raw:g}", f"{ahead:g}")

    def _checked_fids(self):
        fids = []
        for i in range(self.lst_features.count()):
            item = self.lst_features.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                fids.append(item.data(Qt.ItemDataRole.UserRole))
        return fids

    def _set_fid_checked(self, fid, toggle=True):
        for i in range(self.lst_features.count()):
            item = self.lst_features.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == fid:
                if toggle:
                    new = Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked \
                        else Qt.CheckState.Checked
                else:
                    new = Qt.CheckState.Checked
                item.setCheckState(new)
                self.lst_features.scrollToItem(item)
                return item.checkState() == Qt.CheckState.Checked
        return False

    def _calc_crs(self, layer):
        crs = self.crs_widget.crs()
        if crs is not None and crs.isValid():
            return crs
        return layer.crs()

    def _selected_features(self, layer):
        mode = self.cbo_feature_mode.currentIndex()
        if mode == 0:                                # checklist
            fids = self._checked_fids()
            if not fids:
                raise AlignmentError(
                    "Tick at least one line in the list (or click "
                    "'Pick lines on map'), or switch Features mode.")
            req = QgsFeatureRequest().setFilterFids(fids)
            return list(layer.getFeatures(req))
        if mode == 2:                                # current map selection
            feats = list(layer.selectedFeatures())
            if not feats:
                raise AlignmentError(
                    "No features are selected on the alignment layer. "
                    "Select features on the map or use the line list.")
            return feats
        return list(layer.getFeatures())

    def resolve_alignment(self):
        layer = self.cbo_layer.currentLayer()
        if layer is None:
            raise AlignmentError("Choose an alignment layer first.")
        feats = self._selected_features(layer)
        name_field = None
        if self.cbo_name_field.currentIndex() > 0:
            name_field = self.cbo_name_field.currentText()
        mp = MULTIPART_OPTIONS[self.cbo_multipart.currentIndex()][1]
        calc_crs = self._calc_crs(layer)
        alignments, warnings = resolve_alignments(
            layer, feats, calc_crs=calc_crs, name_field=name_field,
            multipart_mode=mp, join_tol=self.spn_join_tol.value(),
            reverse=self.chk_reverse.isChecked(),
            start_chainage=self.spn_start_ch.value())
        self._warns(warnings)
        self.alignments = alignments
        self.alignment = alignments[0]
        if len(alignments) > 1:
            self._log(f"{len(alignments)} alignment(s) resolved — each "
                      "will be processed in turn in this run.")
        a = self.alignment
        fmt = self.cbo_chfmt.currentText()
        self.lbl_align_info.setText(
            f"{a.name}  |  length {a.length:,.1f} m  |  "
            f"{format_chainage(a.start_chainage, fmt)} → "
            f"{format_chainage(a.end_chainage, fmt)}  |  CRS {a.crs_authid}")
        self._update_readback()
        return a

    # ------------------------------------------------------------------ #
    def _mode_enable(self):
        i = self.cbo_mode.currentIndex()
        self.spn_interval.setEnabled(i == 0)
        self.spn_count.setEnabled(i == 1)
        self.txt_chainages.setEnabled(i == 2)
        self.btn_load_csv.setEnabled(i == 2)
        self.btn_click_add.setEnabled(i == 2)
        self.chk_vertices.setEnabled(i == 2)
        self.chk_keep_order.setEnabled(i == 2)

    # ---- width mode (spec 6.16/6.17) ----------------------------------- #
    def _width_mode_enable(self, *_):
        i = self.cbo_width_mode.currentIndex()
        self.spn_left.setEnabled(i in (0, 1))
        self.spn_right.setEnabled(i == 0)
        self.spn_total.setEnabled(i == 2)

    def _effective_widths(self):
        """Resolve (left, right) widths from the current width mode."""
        i = self.cbo_width_mode.currentIndex()
        if i == 1:                                    # equal both sides
            v = self.spn_left.value()
            return v, v
        if i == 2:                                    # total width
            half = self.spn_total.value() / 2.0
            return half, half
        return self.spn_left.value(), self.spn_right.value()

    # ---- inline validation / live read-back (spec 4.32 / 6.02) --------- #
    def _validate_params_inline(self, *_):
        try:
            errors = check_parameters(self.collect_settings(),
                                      self.alignment)
        except Exception:                             # noqa: BLE001
            errors = []
        self.lbl_param_err.setText(" ".join(errors) if errors else "")

    def _on_param_changed(self, *_):
        """Section parameters changed: refresh read-back, validation and
        (when enabled) the debounced auto-preview (spec 6.37)."""
        self._validate_params_inline()
        self._update_readback()
        if self.chk_autopreview.isChecked():
            self._preview_timer.start()

    def _update_readback(self):
        """Live layout read-back on the last resolved alignment."""
        a = self.alignment
        if a is None:
            self.lbl_readback.setText(" ")
            return
        try:
            if self.chk_full.isChecked():
                start, end = a.start_chainage, a.end_chainage
            else:
                s0 = chainage_from_edit(self.edt_proc_start,
                                        a.start_chainage)
                e0 = chainage_from_edit(self.edt_proc_end, a.end_chainage)
                start, end, _ = clamp_chainage_window(a, s0, e0)
            mode = [MODE_INTERVAL, MODE_COUNT, MODE_LIST][
                self.cbo_mode.currentIndex()]
            include = INCLUDE_OPTIONS[self.cbo_include.currentIndex()][1]
            ch, _info, _wns = generate_chainages(
                a, mode, start, end,
                interval=self.spn_interval.value(),
                count=self.spn_count.value(),
                chainage_list=self._chainage_list_values(),
                include=include)
            if not ch:
                self.lbl_readback.setText("count 0")
                return
            fmt = self.cbo_chfmt.currentText()
            last = max(ch)
            remainder = max(end - last, 0.0)
            self.lbl_readback.setText(
                f"count {len(ch)} | first {format_chainage(min(ch), fmt)}"
                f" | last {format_chainage(last, fmt)}"
                f" | remainder {remainder:.2f} m")
        except Exception:                             # noqa: BLE001
            self.lbl_readback.setText(" ")

    def _chainage_list_values(self):
        vals = []
        for line in self.txt_chainages.toPlainText().splitlines():
            line = line.strip().split(",")[0]
            if line:
                vals.append(parse_chainage(line))
        return vals

    def build_sections_now(self):
        """Resolve alignment(s) + generate sections for the first one."""
        a = self.resolve_alignment()
        return self._build_sections_for(a)

    def _build_sections_for(self, a):
        """Generate sections for one resolved AlignmentDef."""
        if self.chk_full.isChecked():
            start, end = a.start_chainage, a.end_chainage
        else:
            s = chainage_from_edit(self.edt_proc_start, a.start_chainage)
            e = chainage_from_edit(self.edt_proc_end, a.end_chainage)
            start, end, wns = clamp_chainage_window(a, s, e)
            self._warns(wns)
        mode = [MODE_INTERVAL, MODE_COUNT, MODE_LIST][
            self.cbo_mode.currentIndex()]
        include = INCLUDE_OPTIONS[self.cbo_include.currentIndex()][1]
        ch, info, warnings = generate_chainages(
            a, mode, start, end,
            interval=self.spn_interval.value(),
            count=self.spn_count.value(),
            chainage_list=self._chainage_list_values(),
            include=include,
            add_vertices=(self.chk_vertices.isChecked() and mode == MODE_LIST),
            preserve_order=(self.chk_keep_order.isChecked()
                            and mode == MODE_LIST))
        self._warns(warnings)
        if "error" in info:
            raise AlignmentError(info["error"])
        if not ch:
            raise AlignmentError("No chainages to generate sections at.")
        s = self.collect_settings()
        sections, warns2 = build_sections(a, ch, s)
        self._warns(warns2)
        errors = check_parameters(s, a)
        if errors:
            raise AlignmentError(" ".join(errors))
        self.sections = sections
        self._status(f"{len(sections)} sections at "
                     f"{info.get('interval', self.spn_interval.value()):g} m"
                     if "interval" in info else f"{len(sections)} sections")
        return sections

    # ------------------------------------------------------------------ #
    def collect_settings(self):
        """All engine-relevant settings from the widgets."""
        s = dict(DEFAULTS)
        left_w, right_w = self._effective_widths()
        s.update({
            "chainage_format": self.cbo_chfmt.currentText(),
            "section_interval": self.spn_interval.value(),
            "width_mode": ["separate", "equal", "total"][
                self.cbo_width_mode.currentIndex()],
            "left_width": left_w,
            "right_width": right_w,
            "total_width": self.spn_total.value(),
            "vertex_handling": ["bisector", "incoming", "outgoing"][
                self.cbo_vertex.currentIndex()],
            "min_segment_length": self.spn_min_seg.value(),
            "station_equations": self._station_equations_from_table(),
            "use_line_z": self.chk_line_z.isChecked(),
            "sampling_interval": self.spn_sample.value(),
            "include_endpoints":
                INCLUDE_OPTIONS[self.cbo_include.currentIndex()][1],
            "tangent_method": TANGENT_METHODS[
                self.cbo_tangent.currentIndex()],
            "tangent_avg_distance": self.spn_tangent_d.value(),
            "angle_offset_deg": self.spn_angle.value(),
            "fixed_bearing_deg": self.spn_bearing.value()
                if self.chk_bearing.isChecked() else None,
            "reverse_normal": self.chk_swap.isChecked(),
            "interp_method": INTERP_METHODS[0],
            "nodata_mode": NODATA_INTERPOLATE
                if self.cbo_nodata.currentIndex() == 1 else NODATA_GAP,
            "nodata_max_gap": self.spn_maxgap.value(),
            "section_prefix": self.edt_prefix.text() or "XS",
            "section_start_number": self.spn_num0.value(),
            "section_number_padding": self.spn_pad.value(),
            "major_every": self.spn_major.value(),
            "line_color": self.col_minor.color().name(),
            "line_color_major": self.col_major.color().name(),
            "line_width_mm": self.spn_w_minor.value(),
            "line_width_major_mm": self.spn_w_major.value(),
            "line_style": self.cbo_pen.currentText(),
            "label_format": self.edt_label_fmt.text(),
            "label_position": self.cbo_label_pos.currentText(),
            "label_font_size": self.spn_label_size.value(),
            "label_major_only": self.chk_label_major.isChecked(),
            "vertical_exaggeration": self.spn_ve.value(),
            "page_size": self.cbo_page.currentText(),
            "gpkg_name": self.edt_gpkg.text() or DEFAULTS["gpkg_name"],
            "timestamped_runs": self.chk_timestamp.isChecked(),
            "output_subfolders": self.chk_subfolders.isChecked(),
            "multipart_mode": MULTIPART_OPTIONS[
                self.cbo_multipart.currentIndex()][1],
            "multipart_join_tol": self.spn_join_tol.value(),
            "pdf_sections_per_page":
                4 if self.cbo_per_page.currentIndex() == 1 else 1,
            "output_dir": self.out_dir.filePath(),
            "logo_path": self.logo_widget.filePath(),
            "output_beside_project": self.chk_beside.isChecked(),
            "vector_format": ["gpkg", "shp", "geojson"][
                self.cbo_vecfmt.currentIndex()],
            "csv_delimiter": [",", ";", "\t"][
                self.cbo_delim.currentIndex()],
            "decimals_chainage": self.spn_dec_ch.value(),
            "decimals_elevation": self.spn_dec_elev.value(),
            "decimals_offset": self.spn_dec_off.value(),
            "export_dpi": self.spn_dpi.value(),
            "excel_per_section": self.chk_xl_per_section.isChecked(),
            "excel_thumbnails": self.chk_xl_thumbs.isChecked(),
            "dxf_text_height": self.spn_dxf_text.value(),
            "dxf_sheet_cols": self.spn_dxf_cols.value(),
            "dxf_layer_prefix": self.edt_dxf_prefix.text()
                or DEFAULTS["dxf_layer_prefix"],
            "convention_fill_above":
                self.cbo_convention.currentIndex() == 0,
        })
        for key, edit in self.tb_fields.items():
            s[key] = edit.text()
        return s

    def apply_settings(self, s):
        """Push a settings dict back into the widgets (presets)."""
        def seti(combo, value, items):
            try:
                combo.setCurrentIndex(items.index(value))
            except ValueError:
                _LOG.debug("Preset value %r not in combo items", value)
        if "chainage_format" in s:
            seti(self.cbo_chfmt, s["chainage_format"], CHAINAGE_FORMATS)
        self.spn_interval.setValue(s.get("section_interval",
                                         DEFAULTS["section_interval"]))
        self.spn_left.setValue(s.get("left_width", DEFAULTS["left_width"]))
        self.spn_right.setValue(s.get("right_width",
                                      DEFAULTS["right_width"]))
        seti(self.cbo_width_mode, s.get("width_mode", "separate"),
             ["separate", "equal", "total"])
        self.spn_total.setValue(s.get("total_width",
                                      DEFAULTS["total_width"]))
        self._width_mode_enable()
        seti(self.cbo_vertex, s.get("vertex_handling", "bisector"),
             ["bisector", "incoming", "outgoing"])
        self.spn_min_seg.setValue(s.get("min_segment_length",
                                        DEFAULTS["min_segment_length"]))
        self._set_station_equations(s.get("station_equations", []))
        self.chk_line_z.setChecked(bool(s.get("use_line_z", False)))
        self.spn_sample.setValue(s.get("sampling_interval", 0.0))
        seti(self.cbo_tangent, s.get("tangent_method", "local"),
             TANGENT_METHODS)
        self.spn_tangent_d.setValue(s.get("tangent_avg_distance", 10.0))
        self.spn_angle.setValue(s.get("angle_offset_deg", 0.0))
        self.chk_swap.setChecked(bool(s.get("reverse_normal", False)))
        self.edt_prefix.setText(s.get("section_prefix", "XS"))
        self.spn_num0.setValue(int(s.get("section_start_number", 1)))
        self.spn_pad.setValue(int(s.get("section_number_padding", 2)))
        self.spn_major.setValue(int(s.get("major_every", 5)))
        self.col_minor.setColor(self._qcolor(
            s.get("line_color", DEFAULTS["line_color"])))
        self.col_major.setColor(self._qcolor(
            s.get("line_color_major", DEFAULTS["line_color_major"])))
        self.spn_w_minor.setValue(s.get("line_width_mm", 0.5))
        self.spn_w_major.setValue(s.get("line_width_major_mm", 0.8))
        seti(self.cbo_pen, s.get("line_style", "solid"),
             ["solid", "dash", "dot", "dash dot"])
        self.edt_label_fmt.setText(s.get("label_format",
                                         DEFAULTS["label_format"]))
        seti(self.cbo_label_pos, s.get("label_position", "left"),
             ["left", "right", "both", "centre"])
        self.spn_label_size.setValue(int(s.get("label_font_size", 8)))
        self.chk_label_major.setChecked(bool(s.get("label_major_only",
                                                   False)))
        self.spn_ve.setValue(s.get("vertical_exaggeration", 10.0))
        seti(self.cbo_page, s.get("page_size", "A3"), ["A3", "A4", "A1"])
        self.edt_gpkg.setText(s.get("gpkg_name", DEFAULTS["gpkg_name"]))
        self.chk_timestamp.setChecked(bool(s.get("timestamped_runs", True)))
        self.chk_subfolders.setChecked(bool(s.get("output_subfolders",
                                                  True)))
        self.chk_beside.setChecked(bool(s.get("output_beside_project",
                                              False)))
        seti(self.cbo_vecfmt, s.get("vector_format", "gpkg"),
             ["gpkg", "shp", "geojson"])
        seti(self.cbo_delim, s.get("csv_delimiter", ","),
             [",", ";", "\t"])
        self.spn_dec_ch.setValue(int(s.get("decimals_chainage",
                                           DEFAULTS["decimals_chainage"])))
        self.spn_dec_elev.setValue(int(s.get(
            "decimals_elevation", DEFAULTS["decimals_elevation"])))
        self.spn_dec_off.setValue(int(s.get("decimals_offset",
                                            DEFAULTS["decimals_offset"])))
        self.spn_dpi.setValue(int(s.get("export_dpi",
                                        DEFAULTS["export_dpi"])))
        self.chk_xl_per_section.setChecked(bool(s.get("excel_per_section",
                                                      False)))
        self.chk_xl_thumbs.setChecked(bool(s.get("excel_thumbnails",
                                                 False)))
        self.spn_dxf_text.setValue(float(s.get(
            "dxf_text_height", DEFAULTS["dxf_text_height"])))
        self.spn_dxf_cols.setValue(int(s.get("dxf_sheet_cols",
                                             DEFAULTS["dxf_sheet_cols"])))
        self.edt_dxf_prefix.setText(str(s.get(
            "dxf_layer_prefix", DEFAULTS["dxf_layer_prefix"])))
        self.cbo_convention.setCurrentIndex(
            0 if s.get("convention_fill_above", True) else 1)
        if s.get("output_dir"):
            self.out_dir.setFilePath(s["output_dir"])
        if s.get("logo_path"):
            self.logo_widget.setFilePath(s["logo_path"])
        for key, edit in self.tb_fields.items():
            if key in s:
                edit.setText(str(s[key] or ""))
        fb = s.get("fixed_bearing_deg")
        self.chk_bearing.setChecked(fb is not None)
        self.spn_bearing.setEnabled(fb is not None)
        if fb is not None:
            try:
                self.spn_bearing.setValue(float(fb))
            except (TypeError, ValueError):
                _LOG.debug("Fixed bearing %r not numeric; spin box keeps "
                           "its value", fb)

    # ------------------------------------------------------------------ #
    def _apply_project_settings(self):
        """Apply project-stored settings over the current widget state.

        Precedence: project > last-used > factory defaults. A sentinel
        key is probed first so projects without stored values leave the
        widgets untouched. Returns True when values were applied."""
        sentinel, ok = QgsProject.instance().readEntry(
            PROJECT_SCOPE, "chainage_format", "")
        if not ok or sentinel == "":
            return False
        s = {k: self.settings.get(k) for k in DEFAULTS}
        self.apply_settings(s)
        self._log("Project-stored settings applied.", Qgis.MessageLevel.Info)
        return True

    def _on_project_read(self, *args):
        """Re-apply project settings when the user opens a project."""
        try:
            self._apply_project_settings()
        except Exception as e:                        # noqa: BLE001
            self._log(f"Project settings not re-applied: {e}",
                      Qgis.MessageLevel.Warning)

    # ================================================================== #
    # alignment tab actions
    # ================================================================== #
    def _start_pick(self):
        layer = self.cbo_layer.currentLayer()
        if layer is None:
            self._log("Choose an alignment layer first.", Qgis.MessageLevel.Warning)
            return
        self._map_tool = AlignmentPickerTool(self.iface.mapCanvas(), layer)
        self._map_tool.feature_picked.connect(self._on_picked)
        self.iface.mapCanvas().setMapTool(self._map_tool)
        self._status("Click a feature on the map…")

    def _on_picked(self, fid):
        self.cbo_feature_mode.setCurrentIndex(0)
        now_checked = self._set_fid_checked(fid, toggle=True)
        n = len(self._checked_fids())
        self._status(f"Line {fid} {'ticked' if now_checked else 'unticked'}"
                     f" — {n} line(s) selected. Keep clicking, or change "
                     "map tool to finish.")

    def _flash_alignment(self):
        try:
            a = self.resolve_alignment()
        except AlignmentError as e:
            self._log(str(e), Qgis.MessageLevel.Warning)
            return
        self.overlay.show_preview(a, [], a.crs_authid)
        QTimer.singleShot(1500, self.overlay.clear)

    def _zoom_alignment(self):
        layer = self.cbo_layer.currentLayer()
        if layer is None:
            return
        try:
            feats = self._selected_features(layer)
        except AlignmentError as e:
            self._log(str(e), Qgis.MessageLevel.Warning)
            return
        from qgis.core import QgsCoordinateTransform, QgsRectangle
        rect = QgsRectangle()
        rect.setMinimal()
        for f in feats:
            if f.geometry():
                rect.combineExtentWith(f.geometry().boundingBox())
        if rect.isEmpty():
            return
        canvas_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        if layer.crs() != canvas_crs:
            tr = QgsCoordinateTransform(layer.crs(), canvas_crs,
                                        QgsProject.instance())
            try:
                rect = tr.transformBoundingBox(rect)
            except Exception:                          # noqa: BLE001
                _LOG.debug("Layer extent transform failed; using layer "
                           "CRS extent", exc_info=True)
        rect.scale(1.15)
        self.iface.mapCanvas().setExtent(rect)
        self.iface.mapCanvas().refresh()

    def _suggest_crs(self):
        """Suggest a projected calculation CRS for a geographic layer."""
        from ..core import crs_engine
        layer = self.cbo_layer.currentLayer()
        if layer is None:
            self._log("Choose an alignment layer first.", Qgis.MessageLevel.Warning)
            return
        if not crs_engine.is_geographic(layer.crs()):
            self._log(f"Layer CRS {layer.crs().authid()} is already "
                      "projected — no suggestion required.", Qgis.MessageLevel.Info)
            return
        crs = crs_engine.suggest_projected_crs(layer)
        if crs is not None and crs.isValid():
            self.crs_widget.setCrs(crs)
            self._log(f"Suggested projected CRS applied: {crs.authid()} "
                      f"({crs.description()}).")
        else:
            self._log("No projected CRS could be suggested for this "
                      "layer.", Qgis.MessageLevel.Warning)

    # ================================================================== #
    # sections tab actions
    # ================================================================== #
    def _load_chainage_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load chainages", "",
            "Tables (*.csv *.txt *.xlsx);;All files (*)")
        if not path:
            return
        if path.lower().endswith(".xlsx"):
            self._load_chainage_xlsx(path)
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                text = fh.read()
        except OSError as e:
            self._log(f"Could not read {path}: {e}", Qgis.MessageLevel.Warning)
            return
        self.txt_chainages.setPlainText(text)
        self.cbo_mode.setCurrentIndex(2)
        self._log(f"Chainage list loaded from {os.path.basename(path)}.")

    def _load_chainage_xlsx(self, path):
        """Load a chainage column from an Excel workbook (first sheet).

        With multiple columns the user picks the chainage column from the
        header row; single-column sheets load directly."""
        try:
            import openpyxl
        except ImportError:
            self._log("openpyxl is not available — cannot read .xlsx "
                      "files. Save the table as CSV instead.",
                      Qgis.MessageLevel.Warning)
            return
        try:
            wb = openpyxl.load_workbook(path, read_only=True,
                                        data_only=True)
            ws = wb.active
            rows = [list(row) for row in ws.iter_rows(values_only=True)]
            wb.close()
        except Exception as e:                        # noqa: BLE001
            self._log(f"Could not read {path}: {e}", Qgis.MessageLevel.Warning)
            return
        rows = [r for r in rows
                if any(c is not None and str(c).strip() for c in r)]
        if not rows:
            self._log("The workbook contains no data.", Qgis.MessageLevel.Warning)
            return
        n_cols = max(len(r) for r in rows)
        col, start_row = 0, 0
        if n_cols > 1:
            headers = []
            for i in range(n_cols):
                h = rows[0][i] if i < len(rows[0]) else None
                text = str(h).strip() if h is not None else ""
                headers.append(text or f"Column {i + 1}")
            choice, ok = QInputDialog.getItem(
                self, "Chainage column",
                "Choose the column containing chainages:", headers,
                0, False)
            if not ok:
                return
            col = headers.index(choice)
            start_row = 1
        values = []
        for r in rows[start_row:]:
            if col < len(r) and r[col] is not None:
                v = str(r[col]).strip()
                if v:
                    values.append(v)
        if not values:
            self._log("No values found in the chosen column.",
                      Qgis.MessageLevel.Warning)
            return
        self.txt_chainages.setPlainText("\n".join(values))
        self.cbo_mode.setCurrentIndex(2)
        self._log(f"{len(values)} chainage value(s) loaded from "
                  f"{os.path.basename(path)}.")

    def _start_click_add(self):
        try:
            a = self.resolve_alignment()
        except AlignmentError as e:
            self._log(str(e), Qgis.MessageLevel.Warning)
            return
        calc = QgsCoordinateReferenceSystem(a.crs_authid)
        self._map_tool = SectionAddTool(self.iface.mapCanvas(), a, calc)
        self._map_tool.chainage_picked.connect(self._on_chainage_click)
        self.iface.mapCanvas().setMapTool(self._map_tool)
        self._status("Click on/near the alignment to add chainages; "
                     "switch map tools to finish.")

    def _on_chainage_click(self, chainage):
        fmt = self.cbo_chfmt.currentText()
        self.txt_chainages.appendPlainText(format_chainage(chainage, fmt))
        self.cbo_mode.setCurrentIndex(2)

    # ================================================================== #
    # DEM tab actions
    # ================================================================== #
    def _project_rasters(self):
        return [lyr for lyr in QgsProject.instance().mapLayers().values()
                if isinstance(lyr, QgsRasterLayer) and lyr.isValid()]

    def _add_dem_from_project(self):
        rasters = self._project_rasters()
        existing = {d.layer_id for d in self.dem_model.dems}
        menu = QMenu(self)
        added = False
        for lyr in rasters:
            if lyr.id() in existing:
                continue
            act = menu.addAction(lyr.name())
            act.setData(lyr.id())
            added = True
        if not added:
            self._log("No further raster layers in the project.",
                      Qgis.MessageLevel.Warning)
            return
        act = menu.exec(self.btn_dem_add.mapToGlobal(
            self.btn_dem_add.rect().bottomLeft()))
        if act is None:
            return
        lyr = QgsProject.instance().mapLayer(act.data())
        self._append_dem(lyr)

    def _add_dem_from_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Add DEM", "",
            "Rasters (*.tif *.tiff *.vrt *.img *.asc);;All files (*)")
        if not path:
            return
        name = os.path.splitext(os.path.basename(path))[0]
        lyr = QgsRasterLayer(path, name)
        if not lyr.isValid():
            self._log(f"Could not load raster: {path}", Qgis.MessageLevel.Warning)
            return
        QgsProject.instance().addMapLayer(lyr)
        self._append_dem(lyr)

    _DEM_COLORS = ["#1976D2", "#388E3C", "#F57C00", "#7B1FA2",
                   "#00838F", "#5D4037", "#C62828", "#9E9D24"]

    def _append_dem(self, lyr):
        color = self._DEM_COLORS[len(self.dem_model.dems)
                                 % len(self._DEM_COLORS)]
        # pixel size shown immediately on add (spec 7.14)
        align_layer = self.cbo_layer.currentLayer()
        calc = self._calc_crs(align_layer) if align_layer else None
        try:
            px = layer_pixel_size(lyr, calc) or 0.0
        except Exception:                             # noqa: BLE001
            px = 0.0
        self.dem_model.add(DemDef(layer_id=lyr.id(), name=lyr.name(),
                                  color=color, pixel_size=px))
        self._log(f"DEM added: {lyr.name()}"
                  + (f" (pixel ≈ {px:g} m)" if px else ""))
        self._sync_dem_combos()

    def _set_dem_indicator(self, warnings):
        """Persistent DEM mismatch indicator on the DEM tab (spec 7.16)."""
        if warnings:
            self.lbl_dem_warn.setText(
                f"⚠ {len(warnings)} DEM issue(s): " + " ".join(warnings))
        else:
            self.lbl_dem_warn.setText("")

    def _check_coverage(self):
        """Check section coverage against the enabled DEM extents
        (spec 7.15)."""
        dems = self.dem_model.enabled_dems()
        if not dems:
            self._log("Add and enable at least one DEM first.",
                      Qgis.MessageLevel.Warning)
            return
        sections = self.sections
        if not sections:
            try:
                sections = self.build_sections_now()
            except AlignmentError as e:
                self._log(f"Coverage check needs sections: {e}",
                          Qgis.MessageLevel.Warning)
                return
        layer_map = {d.layer_id: QgsProject.instance().mapLayer(d.layer_id)
                     for d in dems}
        missing = [d.name for d in dems
                   if layer_map.get(d.layer_id) is None]
        if missing:
            self._log("DEM layer(s) missing from the project: "
                      + ", ".join(missing), Qgis.MessageLevel.Warning)
            return
        calc = QgsCoordinateReferenceSystem(self.alignment.crs_authid) \
            if self.alignment else self._calc_crs(
                self.cbo_layer.currentLayer())
        wns, outside = check_dem_coverage(sections, dems, layer_map, calc)
        wns = list(wns) + check_dem_consistency(dems)
        self._warns(wns)
        self._set_dem_indicator(wns)
        if outside:
            msg = (f"{len(outside)} of {len(sections)} section(s) fall "
                   "completely outside the enabled DEM extents.")
            self.iface.messageBar().pushWarning(PLUGIN_NAME, msg)
            self._log(msg, Qgis.MessageLevel.Warning)
        else:
            msg = (f"All {len(sections)} section(s) intersect the "
                   "enabled DEM extents.")
            self.iface.messageBar().pushSuccess(PLUGIN_NAME, msg)
            self._log(msg)

    def _remove_dem(self):
        rows = {i.row() for i in self.tbl_dems.selectionModel()
                .selectedRows()}
        for r in sorted(rows, reverse=True):
            self.dem_model.remove(r)
        self._sync_dem_combos()

    def _move_dem(self, delta):
        rows = [i.row() for i in self.tbl_dems.selectionModel()
                .selectedRows()]
        if rows:
            self.dem_model.move(rows[0], delta)

    def _suggest_interval(self):
        layers = [QgsProject.instance().mapLayer(d.layer_id)
                  for d in self.dem_model.enabled_dems()]
        layers = [lyr for lyr in layers if lyr]
        if not layers:
            self._log("Add at least one enabled DEM first.", Qgis.MessageLevel.Warning)
            return
        layer = self.cbo_layer.currentLayer()
        calc = self._calc_crs(layer) if layer else None
        v = suggest_sampling_interval(layers, calc)
        self.spn_sample.setValue(v)
        self._log(f"Suggested sampling interval: {v:g} m")

    def _sync_dem_combos(self, *args):
        names = [(d.name, d.layer_id) for d in self.dem_model.enabled_dems()]
        for combo in (self.cbo_ref, self.cbo_cmp):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for n, lid in names:
                combo.addItem(n, lid)
            idx = combo.findData(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        # default reference selection follows the model's Ref checkbox
        for i, d in enumerate(self.dem_model.enabled_dems()):
            if d.is_reference:
                self.cbo_ref.setCurrentIndex(i)
                break

    # ================================================================== #
    # generate / preview
    # ================================================================== #
    def on_preview(self):
        try:
            self.resolve_alignment()
        except AlignmentError as e:
            self._log(str(e), Qgis.MessageLevel.Warning)
            self.iface.messageBar().pushWarning(PLUGIN_NAME, str(e))
            return
        self.overlay.set_colors(self.col_minor.color().name(),
                                self.col_major.color().name())
        total = 0
        for i, a in enumerate(self.alignments):
            try:
                sections = self._build_sections_for(a)
            except AlignmentError as e:
                self._log(f"{a.name}: {e}", Qgis.MessageLevel.Warning)
                continue
            self.overlay.show_preview(a, sections, a.crs_authid,
                                      clear=(i == 0))
            total += len(sections)
        self._status(f"Preview: {total} sections on "
                     f"{len(self.alignments)} alignment(s)")

    def on_generate(self, export_after=False):
        if self._task is not None:
            self._log("A run is already in progress.", Qgis.MessageLevel.Warning)
            return
        # dismiss stale message-bar items from earlier runs so old errors
        # never linger over a clean run
        self.iface.messageBar().clearWidgets()
        try:
            self.resolve_alignment()
        except AlignmentError as e:
            self._log(str(e), Qgis.MessageLevel.Warning)
            self.iface.messageBar().pushWarning(PLUGIN_NAME, str(e))
            return
        dems = self.dem_model.enabled_dems()
        if not dems:
            msg = "Add and enable at least one DEM surface."
            self._log(msg, Qgis.MessageLevel.Warning)
            self.iface.messageBar().pushWarning(PLUGIN_NAME, msg)
            return
        layer_map = {d.layer_id: QgsProject.instance().mapLayer(d.layer_id)
                     for d in dems}
        missing = [d.name for d in dems if layer_map.get(d.layer_id) is None]
        if missing:
            self._log("DEM layer(s) missing from the project: "
                      + ", ".join(missing), Qgis.MessageLevel.Warning)
            return
        # persist the run settings in the project file (JSON-safe values
        # only) so a saved project reopens ready to run
        try:
            safe = {}
            for k, v in self.collect_settings().items():
                try:
                    json.dumps(v)
                except (TypeError, ValueError):
                    _LOG.debug("Setting %r not JSON-serialisable; not "
                               "stored in project", k)
                    continue
                safe[k] = v
            self.settings.save_all_project(safe)
        except Exception as e:                        # noqa: BLE001
            self._log(f"Project settings not saved: {e}", Qgis.MessageLevel.Warning)
        self._align_queue = list(self.alignments)
        self._batch_export = export_after
        self._batch_total = len(self._align_queue)
        self._busy(True)
        self._start_next_alignment()

    def _start_next_alignment(self):
        """Run the next alignment in the batch queue."""
        if not self._align_queue:
            self._busy(False)
            return
        a = self._align_queue.pop(0)
        self.alignment = a
        try:
            sections = self._build_sections_for(a)
        except AlignmentError as e:
            self._log(f"{a.name}: {e}", Qgis.MessageLevel.Warning)
            self._start_next_alignment()
            return
        self._start_sampling(a, sections)

    def _start_sampling(self, a, sections):
        """Sample the enabled DEMs along `sections` of alignment `a`."""
        dems = self.dem_model.enabled_dems()
        layer_map = {d.layer_id: QgsProject.instance().mapLayer(d.layer_id)
                     for d in dems}
        calc = QgsCoordinateReferenceSystem(a.crs_authid)
        wns, outside = check_dem_coverage(sections, dems, layer_map, calc)
        dem_wns = list(wns) + check_dem_consistency(dems)
        self._warns(dem_wns)
        self._set_dem_indicator(dem_wns)
        s = self.collect_settings()
        interval = s["sampling_interval"]
        if interval <= 0:
            interval = suggest_sampling_interval(
                [layer_map[d.layer_id] for d in dems], calc)
            self._log(f"Auto sampling interval: {interval:g} m")
        use_line_z = bool(s.get("use_line_z")) \
            and getattr(a, "vertex_z", None) is not None
        self.overlay.set_colors(s["line_color"], s["line_color_major"])
        self.overlay.show_preview(a, sections, a.crs_authid)
        self._out_mgr = None          # each alignment gets its own run dir
        # result cache: identical (alignment, sections, DEMs, interval)
        # runs are reused without resampling
        cached = None
        try:
            cached = self._profile_cache.get(
                a, sections, dems, interval,
                extra=(s["nodata_mode"], s["nodata_max_gap"],
                       use_line_z))
        except Exception:                             # noqa: BLE001
            cached = None
        if cached is not None:
            profiles, long_profile = cached
            self._log("Cache hit — reusing sampled profiles (no "
                      "resampling required).")
            self._apply_run_results(a, sections, profiles, long_profile,
                                    elapsed=0.0,
                                    export_after=self._batch_export,
                                    from_cache=True)
            return
        done = max(self._batch_total - len(self._align_queue), 1)
        self._task = GenerateAndSampleTask(
            f"{PLUGIN_NAME}: {a.name} ({done}/{self._batch_total})",
            a, sections, dems, layer_map, calc, interval,
            s["nodata_mode"], s["nodata_max_gap"],
            use_line_z=use_line_z)
        self._task.progressChanged.connect(
            lambda p: self.progress.setValue(int(p)))
        self._task.taskCompleted.connect(
            functools.partial(self._on_sampled, self._batch_export))
        self._task.taskTerminated.connect(self._on_sample_failed)
        self._status(f"[{done}/{self._batch_total}] {a.name}: sampling "
                     f"{len(sections)} sections × {len(dems)} DEMs…")
        QgsApplication.taskManager().addTask(self._task)

    # ------------------------------------------------------------------ #
    def on_generate_sections_only(self):
        """Build sections, preview them and add a sections layer to the
        map without sampling any DEM (spec 4.10)."""
        if self._task is not None:
            self._log("A run is already in progress.", Qgis.MessageLevel.Warning)
            return
        try:
            self.resolve_alignment()
        except AlignmentError as e:
            self._log(str(e), Qgis.MessageLevel.Warning)
            self.iface.messageBar().pushWarning(PLUGIN_NAME, str(e))
            return
        from ..export.vector_exporter import sections_to_memory_layer
        s = self.collect_settings()
        self.overlay.set_colors(s["line_color"], s["line_color_major"])
        total = 0
        for i, a in enumerate(self.alignments):
            try:
                sections = self._build_sections_for(a)
            except AlignmentError as e:
                self._log(f"{a.name}: {e}", Qgis.MessageLevel.Warning)
                continue
            self.alignment = a
            self.sections = sections
            self.overlay.show_preview(a, sections, a.crs_authid,
                                      clear=(i == 0))
            sec_layer = sections_to_memory_layer(
                sections, a.crs_authid, f"Sections — {a.name}")
            apply_section_style(sec_layer, s)
            apply_section_labels(sec_layer, s)
            QgsProject.instance().addMapLayer(sec_layer)
            total += len(sections)
        self._status(f"{total} section(s) generated — no sampling")
        self._log(f"Sections generated without sampling: {total} on "
                  f"{len(self.alignments)} alignment(s). Use 'Extract "
                  "profiles' to sample the DEMs along them.")
        self._update_summary()

    def on_extract(self):
        """Sample the enabled DEMs along the CURRENT sections
        (spec 4.11)."""
        if self._task is not None:
            self._log("A run is already in progress.", Qgis.MessageLevel.Warning)
            return
        if not self.sections or self.alignment is None:
            msg = ("No sections available — run 'Generate sections' "
                   "(or 'Generate') first.")
            self._log(msg, Qgis.MessageLevel.Warning)
            self.iface.messageBar().pushWarning(PLUGIN_NAME, msg)
            return
        dems = self.dem_model.enabled_dems()
        if not dems:
            msg = "Add and enable at least one DEM surface."
            self._log(msg, Qgis.MessageLevel.Warning)
            self.iface.messageBar().pushWarning(PLUGIN_NAME, msg)
            return
        layer_map = {d.layer_id: QgsProject.instance().mapLayer(d.layer_id)
                     for d in dems}
        missing = [d.name for d in dems
                   if layer_map.get(d.layer_id) is None]
        if missing:
            self._log("DEM layer(s) missing from the project: "
                      + ", ".join(missing), Qgis.MessageLevel.Warning)
            return
        self._align_queue = []
        self._batch_export = False
        self._batch_total = 1
        self._busy(True)
        self._start_sampling(self.alignment, self.sections)

    def on_reset_settings(self):
        """Restore factory defaults for every setting (spec 11.10)."""
        self.apply_settings(dict(DEFAULTS))
        self.tbl_steq.setRowCount(0)
        self._log("All settings reset to factory defaults.")

    def _on_sample_failed(self):
        task, self._task = self._task, None
        err = (task.error if task else None) or "Sampling failed."
        self._log(err, Qgis.MessageLevel.Critical)
        self.iface.messageBar().pushCritical(PLUGIN_NAME,
                                             err.splitlines()[-1])
        if self._align_queue and "Cancelled" not in err:
            self._log("Continuing with the next alignment in the batch.")
            self._start_next_alignment()
        else:
            self._align_queue = []
            self._busy(False)
            self._status("Failed")

    def _on_sampled(self, export_after):
        task, self._task = self._task, None
        self._busy(False)
        if task is None:
            return
        # cache the sampled result for identical re-runs
        try:
            self._profile_cache.put(
                (task.profiles, task.long_profile), task.alignment,
                task.sections, self.dem_model.enabled_dems(),
                task.sampling_interval,
                extra=(task.nodata_mode, task.nodata_max_gap,
                       getattr(task, "use_line_z", False)))
        except Exception:                             # noqa: BLE001
            _LOG.debug("Result caching failed; next run will resample",
                       exc_info=True)
        self._warns(sorted(set(task.warnings))[:50])
        self._apply_run_results(task.alignment, task.sections,
                                task.profiles, task.long_profile,
                                elapsed=task.elapsed_s,
                                export_after=export_after,
                                from_cache=False)

    def _apply_run_results(self, alignment, sections, profiles,
                           long_profile, elapsed=0.0, export_after=False,
                           from_cache=False):
        """Load a sampled run (fresh or cached) into the panel and
        continue the batch queue."""
        self.alignment = alignment
        self.profiles = profiles
        self.sections = sections
        self.long_profile = long_profile
        self._last_sampling_s = elapsed
        dems = self.dem_model.enabled_dems()
        # pseudo-surface: alignment Z values sampled as a design grade —
        # give the viewer legend a DemDef-like entry (NOT the DEM table)
        plot_dems = list(dems)
        has_line_z = any(
            LINE_Z_SURFACE_ID in (p.lines or {}) for p in (profiles or []))
        if not has_line_z and long_profile is not None:
            has_line_z = LINE_Z_SURFACE_ID in (long_profile.lines or {})
        if has_line_z:
            plot_dems.append(DemDef(
                layer_id=LINE_Z_SURFACE_ID, name="Alignment Z",
                color="#000000", line_style="dash dot", enabled=True))
        self.controller.load_run(self.profiles, self.long_profile,
                                 plot_dems)
        if hasattr(self.plot, "set_context"):
            try:
                self.plot.set_context(alignment.name)
            except Exception:                         # noqa: BLE001
                _LOG.debug("Plot context label not set", exc_info=True)
        self.cbo_section.blockSignals(True)
        self.cbo_section.clear()
        self.cbo_section.addItems(self.controller.labels())
        self.cbo_section.blockSignals(False)
        if from_cache:
            self._status(f"Reused {len(self.profiles)} cached sections")
            self._log(f"Cache hit — reusing sampled profiles for "
                      f"'{alignment.name}' ({len(self.profiles)} "
                      f"sections, {len(dems)} DEM(s)).")
        else:
            self._status(f"Sampled {len(self.profiles)} sections in "
                         f"{elapsed:.1f} s")
            self._log(f"Run complete: {len(self.profiles)} sections, "
                      f"{len(dems)} DEM(s), {elapsed:.1f} s.")
        self._update_summary()
        self._add_result_layers()
        if self.cbo_ref.count() >= 2 or (self.cbo_ref.count() >= 1
                                         and self.cbo_cmp.count() >= 1):
            self.compute_comparisons(quiet=True)
        self.tabs.setCurrentIndex(3)      # viewer
        if export_after:
            self.on_export()
        if self._align_queue:
            self._log(f"Alignment '{self.alignment.name}' done — "
                      f"{len(self._align_queue)} remaining in this run.")
            self._start_next_alignment()
        else:
            self._busy(False)
            if self._batch_total > 1:
                self._log(f"Batch complete: {self._batch_total} "
                          "alignments processed. The viewer shows the "
                          "last alignment; result layers for every "
                          "alignment are in the Layers panel.")

    def _resolve_output_dir(self, s):
        """Base output directory: beside the saved project when requested
        (spec 10.03), else the Outputs-tab directory. May return ''."""
        if s.get("output_beside_project"):
            proj_dir = QgsProject.instance().absolutePath()
            if proj_dir:
                return os.path.join(proj_dir, "profile_outputs")
            self._log("'Beside the QGIS project' is ticked but the "
                      "project has not been saved — using the output "
                      "directory instead.", Qgis.MessageLevel.Warning)
        return s.get("output_dir") or ""

    @staticmethod
    def _vec_write(layer, path, layer_name, fmt):
        """Write `layer` with write_gpkg/write_vector; returns (ok, err)."""
        from ..export.vector_exporter import write_gpkg
        if fmt == "gpkg":
            return write_gpkg(layer, path, layer_name)
        from ..export.vector_exporter import write_vector
        res = write_vector(layer, path, layer_name, fmt)
        if isinstance(res, tuple):
            return res
        return bool(res), None

    def _add_result_layers(self):
        from ..export.vector_exporter import (sections_to_memory_layer,
                                              sample_points_to_memory_layer)
        s = self.collect_settings()
        dems = self.dem_model.enabled_dems()
        a = self.alignment
        sec_layer = sections_to_memory_layer(
            self.sections, a.crs_authid, f"Sections — {a.name}")
        pt_layer = sample_points_to_memory_layer(
            self.profiles, dems, a.crs_authid, f"Profile points — {a.name}")
        apply_section_style(sec_layer, s)
        apply_section_labels(sec_layer, s)
        base = self._resolve_output_dir(s)
        if not self.chk_memory.isChecked() and base:
            self._out_mgr = OutputManager(
                base, timestamped=s["timestamped_runs"],
                subfolders=s["output_subfolders"])
            fmt = s.get("vector_format", "gpkg")
            if fmt == "gpkg":
                gpkg = self._out_mgr.path_for("gis", s["gpkg_name"])
                ok1, err1 = self._vec_write(sec_layer, gpkg, "sections",
                                            fmt)
                ok2, err2 = self._vec_write(pt_layer, gpkg,
                                            "profile_points", fmt)
                target = gpkg
            else:
                p1 = self._out_mgr.path_for("gis", f"sections.{fmt}")
                p2 = self._out_mgr.path_for("gis",
                                            f"profile_points.{fmt}")
                ok1, err1 = self._vec_write(sec_layer, p1, "sections",
                                            fmt)
                ok2, err2 = self._vec_write(pt_layer, p2,
                                            "profile_points", fmt)
                target = os.path.dirname(p1)
            if ok1 and ok2:
                self._log(f"Results written to {target}")
            else:
                self._log(f"Vector write issue: {err1 or ''} "
                          f"{err2 or ''}", Qgis.MessageLevel.Warning)
        group = QgsProject.instance().layerTreeRoot().insertGroup(
            0, f"Advanced Profile — {a.name}")
        for lyr in (sec_layer, pt_layer):
            QgsProject.instance().addMapLayer(lyr, False)
            group.addLayer(lyr)

    def on_cancel(self):
        self._align_queue = []
        self._img_cancel = True       # stops the main-thread image loop
        for t in (self._task, self._export_task):
            if t is not None:
                t.cancel()
        self._status("Cancelling…")

    def on_reset(self):
        self.overlay.clear()
        self.sections = []
        self.profiles = []
        self.long_profile = None
        self.comparisons = []
        self.volume_rows = []
        self.controller.clear()
        self.cbo_section.clear()
        self.tbl_comp.setRowCount(0)
        self.tbl_vol.setRowCount(0)
        self.lbl_totals.setText("No volumes computed")
        self.lbl_warn_summary.setText("No warnings")
        self.lbl_summary.setText("No results yet")
        self._status("Idle")

    # ================================================================== #
    # viewer actions
    # ================================================================== #
    def _on_section_combo(self, i):
        if i >= 0:
            self.controller.go(i)
            self.cbo_view_mode.setCurrentIndex(0)

    def _nav_prev(self):
        self.controller.prev()
        self._sync_section_combo()

    def _nav_next(self):
        self.controller.next()
        self._sync_section_combo()

    def _sync_section_combo(self):
        self.cbo_section.blockSignals(True)
        self.cbo_section.setCurrentIndex(self.controller.index)
        self.cbo_section.blockSignals(False)

    def _datum_changed(self, *_):
        self.plot.set_datum(
            self.spn_datum.value() if self.chk_datum.isChecked() else None)

    def _shade_changed(self, *_):
        if self.chk_shade.isChecked() and self.cbo_ref.count() \
                and self.cbo_cmp.count():
            self.plot.set_shading((self.cbo_ref.currentData(),
                                   self.cbo_cmp.currentData()))
        else:
            self.plot.set_shading(None)

    def _diff_changed(self, *_):
        """Toggle the Δz (comparison − reference) profile view."""
        if self.chk_diff.isChecked() and self.cbo_ref.count() \
                and self.cbo_cmp.count():
            self.plot.set_difference_pair((self.cbo_ref.currentData(),
                                           self.cbo_cmp.currentData()))
        else:
            self.plot.set_difference_pair(None)
        self.controller.refresh()

    def _on_plot_hover(self, x, readout):
        parts = []
        p = self.controller.current()
        fmt = self.cbo_chfmt.currentText()
        if self.controller.mode == "ls":
            parts.append(format_chainage(x, fmt))
        else:
            parts.append(f"offset {x:+.2f} m")
        for name, v in readout.items():
            parts.append(f"{name}: {v:.3f}")
        self.lbl_readout.setText("   ".join(parts))
        # map marker sync
        if p is None or p.xs is None or not len(p.xs):
            return
        xa = np.asarray(p.offsets, dtype=float)
        if x < xa[0] or x > xa[-1]:
            self.overlay.hide_position()
            return
        wx = float(np.interp(x, xa, np.asarray(p.xs, dtype=float)))
        wy = float(np.interp(x, xa, np.asarray(p.ys, dtype=float)))
        self.overlay.show_position(wx, wy)

    # ================================================================== #
    # comparison / volumes
    # ================================================================== #
    def compute_comparisons(self, quiet=False):
        if not self.profiles:
            if not quiet:
                self._log("Generate sections first.", Qgis.MessageLevel.Warning)
            return
        ref = self.cbo_ref.currentData()
        cmp_ = self.cbo_cmp.currentData()
        if not ref or not cmp_ or ref == cmp_:
            if not quiet:
                self._log("Choose two different surfaces to compare.",
                          Qgis.MessageLevel.Warning)
            return
        # convention selector (spec 9.03): when 'fill = reference above
        # comparison' is chosen the surfaces are swapped for the maths
        fill_above = self.cbo_convention.currentIndex() == 0
        r_id, c_id = (ref, cmp_) if fill_above else (cmp_, ref)
        self.comparisons = [compare_section(p, r_id, c_id)
                            for p in self.profiles]
        rows, totals, skipped = average_end_area(
            self.comparisons,
            prismoidal=self.chk_prismoidal.isChecked())
        self.volume_rows, self.volume_totals = rows, totals
        self._warns(skipped)
        fmt = self.cbo_chfmt.currentText()
        # |Δz| threshold check per section (comparison − reference)
        from qgis.PyQt.QtGui import QBrush, QColor
        tol = self.spn_dz_tol.value()
        highlight = QBrush(QColor("#FFE0B2"))          # light orange
        exceed = []
        for p in self.profiles:
            spans = []
            rl, cl = p.lines.get(r_id), p.lines.get(c_id)
            if rl is not None and cl is not None \
                    and rl.elevations is not None \
                    and cl.elevations is not None:
                spans = threshold_exceedances(
                    p.offsets, difference(rl.elevations, cl.elevations),
                    tol)
            exceed.append(spans)
        self.tbl_comp.setRowCount(len(self.comparisons))
        for r, c in enumerate(self.comparisons):
            vals = [c.label, format_chainage(c.chainage, fmt),
                    f"{c.cut_area:.2f}", f"{c.fill_area:.2f}",
                    f"{c.net_area:+.2f}", f"{c.gap_length:.1f}"]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if not c.valid:
                    item.setForeground(Qt.GlobalColor.red)
                if r < len(exceed) and exceed[r]:
                    item.setBackground(highlight)
                self.tbl_comp.setItem(r, col, item)
        n_exc = sum(1 for sp in exceed if sp)
        if n_exc:
            samples = []
            for c, sp in zip(self.comparisons, exceed):
                if sp:
                    o0, o1, mx = sp[0]
                    samples.append(f"{c.label} ({o0:+.1f} to {o1:+.1f} m, "
                                   f"max {mx:.2f} m)")
                if len(samples) >= 3:
                    break
            more = "" if n_exc <= 3 else f" (+{n_exc - 3} more)"
            self._log(f"|Δz| > {tol:g} m on {n_exc} section(s): "
                      + "; ".join(samples) + more + ".", Qgis.MessageLevel.Warning)
        self.tbl_vol.setRowCount(len(rows))
        for r, vr in enumerate(rows):
            vals = [format_chainage(vr.from_chainage, fmt),
                    format_chainage(vr.to_chainage, fmt),
                    f"{vr.length:.1f}", f"{vr.cut_volume:.1f}",
                    f"{vr.fill_volume:.1f}", f"{vr.net_volume:+.1f}",
                    f"{vr.cum_cut:.1f}", f"{vr.cum_fill:.1f}"]
            for col, val in enumerate(vals):
                self.tbl_vol.setItem(r, col, QTableWidgetItem(val))
        conv_txt = ("fill where comparison is above reference"
                    if fill_above else
                    "fill where reference is above comparison "
                    "(surfaces swapped)")
        self.lbl_totals.setText(
            f"Totals — cut {totals['cut']:,.1f} m³, "
            f"fill {totals['fill']:,.1f} m³, net {totals['net']:+,.1f} m³ "
            f"({totals['spans']} spans). Convention: {conv_txt}.")
        self._shade_changed()
        self._diff_changed()
        if not quiet:
            self.tabs.setCurrentIndex(4)

    def _show_mass_haul(self):
        """Modal mass-haul diagram (cumulative net volume vs chainage)."""
        from ..core.volume_engine import mass_haul
        pts = mass_haul(self.volume_rows)
        if not pts:
            self._log("Compute comparison and volumes first — no volume "
                      "rows for a mass-haul diagram.", Qgis.MessageLevel.Warning)
            return
        from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
        dlg = QDialog(self)
        dlg.setWindowTitle("Mass-haul diagram")
        dlg.resize(760, 460)
        lay = QVBoxLayout(dlg)
        fig = Figure(figsize=(7.4, 4.0), tight_layout=True)
        canvas = FigureCanvasQTAgg(fig)
        ax = fig.add_subplot(111)
        xs = [self.volume_rows[0].from_chainage] + [c for c, _ in pts]
        ys = [0.0] + [v for _, v in pts]
        ax.plot(xs, ys, color="#1976D2", linewidth=1.4)
        ax.axhline(0.0, color="#666666", linewidth=0.8)
        ax.grid(True, linewidth=0.5, alpha=0.6)
        ax.set_xlabel("Chainage (m)")
        ax.set_ylabel("Cumulative net volume (m³)")
        name = self.alignment.name if self.alignment else ""
        ax.set_title(f"Mass-haul diagram — {name}" if name
                     else "Mass-haul diagram")
        lay.addWidget(canvas, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

    # ================================================================== #
    # export
    # ================================================================== #
    def on_export(self):
        if not self.profiles:
            msg = "Nothing to export — run Generate first."
            self._log(msg, Qgis.MessageLevel.Warning)
            self.iface.messageBar().pushWarning(PLUGIN_NAME, msg)
            return
        s = self.collect_settings()
        base = self._resolve_output_dir(s)
        if not base:
            msg = "Choose an output directory on the Outputs tab."
            self._log(msg, Qgis.MessageLevel.Warning)
            self.iface.messageBar().pushWarning(PLUGIN_NAME, msg)
            self.tabs.setCurrentIndex(7)
            return
        if self._out_mgr is None:
            self._out_mgr = OutputManager(
                base, timestamped=s["timestamped_runs"],
                subfolders=s["output_subfolders"])
        om = self._out_mgr
        self._last_run_dir = om.run_dir()
        self._add_recent_run(self._last_run_dir)
        dems = self.dem_model.enabled_dems()
        conv_txt = ("fill = comparison above reference"
                    if s.get("convention_fill_above", True)
                    else "fill = reference above comparison")
        meta = {"Alignment": self.alignment.name,
                "Section interval (m)": s["section_interval"],
                "Sampling interval (m)": s["sampling_interval"] or "auto",
                "Cut/fill convention": conv_txt}
        jobs = []
        if self.chk_f_csv.isChecked():
            if self.long_profile:
                jobs.append(("CSV long section", functools.partial(
                    export_long_section,
                    om.path_for("csv", "long_section.csv"),
                    self.long_profile, dems, s, metadata=meta)))
            jobs.append(("CSV cross-sections", functools.partial(
                export_cross_sections,
                om.path_for("csv", "cross_sections.csv"),
                self.profiles, dems, s, metadata=meta)))
            if self.comparisons:
                jobs.append(("CSV comparison", functools.partial(
                    export_comparison,
                    om.path_for("csv", "comparison.csv"),
                    self.comparisons, s, metadata=meta)))
            if self.volume_rows:
                jobs.append(("CSV volumes", functools.partial(
                    export_volumes, om.path_for("csv", "volumes.csv"),
                    self.volume_rows, self.volume_totals, s,
                    metadata=meta)))
        images_dir = os.path.dirname(om.path_for("images", "x.png"))
        if self.chk_f_xlsx.isChecked():
            from ..export.excel_exporter import (export_workbook,
                                                 OPENPYXL_AVAILABLE,
                                                 OPENPYXL_MISSING_MSG)
            if not OPENPYXL_AVAILABLE:
                self._log(OPENPYXL_MISSING_MSG, Qgis.MessageLevel.Warning)
            else:
                run = {"long_section": self.long_profile,
                       "cross_sections": self.profiles,
                       "comparisons": self.comparisons or None,
                       "volumes": (self.volume_rows, self.volume_totals)
                       if self.volume_rows else None,
                       "dem_defs": dems,
                       "alignment": self.alignment,
                       "metadata": {
                           "Plugin": PLUGIN_NAME,
                           "Alignment": self.alignment.name,
                           "Sections": len(self.profiles),
                           "Sampling interval (m)":
                               s["sampling_interval"] or "auto",
                           "Cut/fill convention": conv_txt,
                           "Generated": self._status_time()}}
                jobs.append(("Excel workbook", functools.partial(
                    export_workbook,
                    om.path_for("excel", "profile_results.xlsx"), run, s,
                    images_dir=images_dir)))
        if self.chk_f_dxfg.isChecked():
            from ..export.dxf_exporter import export_geometry_dxf
            jobs.append(("DXF geometry", functools.partial(
                export_geometry_dxf,
                om.path_for("dxf", "profiles_geometry.dxf"),
                self.profiles, dems, s)))
        if self.chk_f_dxfs.isChecked():
            from ..export.dxf_exporter import export_sheet_dxf
            jobs.append(("DXF sheets", functools.partial(
                export_sheet_dxf,
                om.path_for("dxf", "section_sheets.dxf"),
                self.profiles, dems, s,
                comparisons=self.comparisons or None)))
        if self.chk_f_dxfw.isChecked():
            from ..export.dxf_exporter import export_geometry_dxf
            jobs.append(("DXF plan (world coords)", functools.partial(
                export_geometry_dxf,
                om.path_for("dxf", "profiles_plan_world.dxf"),
                self.profiles, dems, s, world=True)))
        # ---- main-thread exports: images + pdf --------------------------
        image_paths = []
        dpi = int(s.get("export_dpi", 200))
        self._img_cancel = False       # reset the cancel flag (spec 4.34)

        def _img_progress(p):
            from qgis.PyQt.QtWidgets import QApplication
            self.progress.setValue(int(p))
            QApplication.processEvents()

        if self.chk_f_png.isChecked() or self.chk_f_pdf.isChecked():
            self._status("Rendering plot images…")
            self.progress.setValue(0)
            image_paths = self.controller.export_all_images(
                images_dir, ext="png", dpi=dpi, progress=_img_progress,
                is_cancelled=lambda: self._img_cancel)
        if self.chk_f_svg.isChecked() and not self._img_cancel:
            self._status("Rendering SVG plots…")
            self.controller.export_all_images(
                images_dir, ext="svg", dpi=dpi, progress=_img_progress,
                is_cancelled=lambda: self._img_cancel)
        if self._img_cancel:
            self._log("Image rendering cancelled by the user.",
                      Qgis.MessageLevel.Warning)
        if self.chk_f_pdf.isChecked() and image_paths:
            try:
                from ..export.pdf_exporter import make_pdf_composer
                tb = default_title_block(s, self.alignment.name)
                for key in list(self.tb_fields):
                    setattr(tb, key.replace("tb_", ""),
                            s.get(key, "") or getattr(tb,
                                                      key.replace("tb_", ""),
                                                      ""))
                pdf = make_pdf_composer(
                    om.path_for("pdf", "section_sheets.pdf"),
                    page=s["page_size"], landscape=True,
                    title_block=tb.as_dict(),
                    logo_path=s.get("logo_path") or None,
                    sections_per_page=s["pdf_sections_per_page"])
                fmt = self.cbo_chfmt.currentText()
                ve = s["vertical_exaggeration"]
                half_w = max(s["left_width"], s["right_width"])
                scale_text = f"VE {ve:g}× | offsets ±{half_w:g} m"
                for p, img in zip(self.profiles, image_paths):
                    pdf.add_profile_image(
                        img, p.label, format_chainage(p.chainage, fmt),
                        extra_notes=f"Vertical exaggeration {ve:g}×",
                        scale_text=scale_text)
                pdf.save()
                self._log(f"PDF written: {pdf.path if hasattr(pdf, 'path') else 'section_sheets.pdf'}")
            except Exception as e:                    # noqa: BLE001
                self._log(f"PDF export failed: {e}", Qgis.MessageLevel.Warning)
        if self.chk_f_layout.isChecked():
            try:
                from qgis.core import QgsRectangle
                from ..export.layout_exporter import build_profile_layout
                ls_img = os.path.join(images_dir, "long_section.png")
                if not os.path.isfile(ls_img):
                    ls_img = image_paths[-1] if image_paths else None
                xs = [v[0] for v in self.alignment.vertices]
                ys = [v[1] for v in self.alignment.vertices]
                rect = QgsRectangle(min(xs), min(ys), max(xs), max(ys))
                tb = default_title_block(s, self.alignment.name).as_dict()
                build_profile_layout(
                    f"Advanced Profile — {self.alignment.name}",
                    rect, ls_img, title_block=tb, page=s["page_size"],
                    logo_path=s.get("logo_path") or None)
                self._log("Print layout created — open it via "
                          "Project ▸ Layouts.")
            except Exception as e:                    # noqa: BLE001
                self._log(f"Print layout creation failed: {e}",
                          Qgis.MessageLevel.Warning)
        if self.chk_f_gpkg.isChecked() and self.chk_memory.isChecked():
            # user kept layers in memory but still wants a GeoPackage copy
            self._add_result_layers_gpkg_only(om, s)
        if self.chk_f_gpkg.isChecked() and self.comparisons:
            self._export_difference_points(om, s)
        om.manifest({"settings": {k: v for k, v in s.items()
                                  if k != "output_dir"},
                     "alignment": self.alignment.name,
                     "sections": len(self.profiles)})
        if not jobs:
            self._status("Export finished (no background jobs)")
            self._log(f"Outputs in {om.run_dir()}")
            self._update_summary()
            return
        self._export_task = ExportTask(f"{PLUGIN_NAME}: exporting", jobs)
        self._export_task.progressChanged.connect(
            lambda p: self.progress.setValue(int(p)))
        self._export_task.taskCompleted.connect(self._on_exported)
        self._export_task.taskTerminated.connect(self._on_exported)
        self._busy(True)
        self._status(f"Exporting {len(jobs)} file(s)…")
        QgsApplication.taskManager().addTask(self._export_task)

    def _add_result_layers_gpkg_only(self, om, s):
        from ..export.vector_exporter import (sections_to_memory_layer,
                                              sample_points_to_memory_layer)
        dems = self.dem_model.enabled_dems()
        a = self.alignment
        fmt = s.get("vector_format", "gpkg")
        sec = sections_to_memory_layer(self.sections, a.crs_authid,
                                       "sections")
        pts = sample_points_to_memory_layer(
            self.profiles, dems, a.crs_authid, "points")
        if fmt == "gpkg":
            gpkg = om.path_for("gis", s["gpkg_name"])
            self._vec_write(sec, gpkg, "sections", fmt)
            self._vec_write(pts, gpkg, "profile_points", fmt)
        else:
            self._vec_write(sec, om.path_for("gis", f"sections.{fmt}"),
                            "sections", fmt)
            self._vec_write(pts,
                            om.path_for("gis", f"profile_points.{fmt}"),
                            "profile_points", fmt)

    def _export_difference_points(self, om, s):
        """Write Δz difference points alongside the section outputs
        (spec 10.07)."""
        try:
            from ..export.vector_exporter import \
                difference_points_to_memory_layer
            ref = self.cbo_ref.currentData()
            cmp_ = self.cbo_cmp.currentData()
            if not ref or not cmp_ or ref == cmp_:
                return
            if not s.get("convention_fill_above", True):
                ref, cmp_ = cmp_, ref
            lyr = difference_points_to_memory_layer(
                self.profiles, ref, cmp_, self.alignment.crs_authid,
                "difference_points")
            fmt = s.get("vector_format", "gpkg")
            if fmt == "gpkg":
                path = om.path_for("gis", s["gpkg_name"])
            else:
                path = om.path_for("gis", f"difference_points.{fmt}")
            ok, err = self._vec_write(lyr, path, "difference_points",
                                      fmt)
            if ok:
                self._log(f"Difference points written to {path}")
            else:
                self._log(f"Difference points write issue: {err or ''}",
                          Qgis.MessageLevel.Warning)
        except Exception as e:                        # noqa: BLE001
            self._log(f"Difference points export failed: {e}",
                      Qgis.MessageLevel.Warning)

    def _on_exported(self):
        task, self._export_task = self._export_task, None
        self._busy(False)
        if task is None:
            return
        for name, path in task.results:
            self._log(f"Exported {name}: {path}")
        for name, err in task.failures:
            self._log(f"Export FAILED — {name}:\n{err}", Qgis.MessageLevel.Critical)
        n_ok, n_bad = len(task.results), len(task.failures)
        self._last_export_s = getattr(task, "elapsed_s", None)
        self._update_summary()
        self._status(f"Export complete: {n_ok} ok"
                     + (f", {n_bad} failed" if n_bad else ""))
        bar = self.iface.messageBar()
        if n_bad:
            bar.pushWarning(PLUGIN_NAME,
                            f"Export finished with {n_bad} failure(s) — "
                            "see the Log tab.")
        else:
            bar.pushSuccess(PLUGIN_NAME,
                            f"{n_ok} export(s) written to "
                            f"{self._out_mgr.run_dir()}")

    def _open_output_folder(self):
        target = None
        if self._out_mgr is not None:
            target = self._out_mgr.run_dir()
        elif self.out_dir.filePath():
            target = self.out_dir.filePath()
        if target and os.path.isdir(target):
            # Open in the system file manager via Qt (cross-platform; no
            # external process is spawned from plugin code).
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(os.path.normpath(target)))
        else:
            self._log("No output folder yet.", Qgis.MessageLevel.Warning)

    @staticmethod
    def _status_time():
        import datetime
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # ================================================================== #
    # presets
    # ================================================================== #
    def _refresh_presets(self):
        self.lst_presets.clear()
        self.lst_presets.addItems(self.presets.list_presets())

    # ---- recent run folders (spec 11.12) -------------------------------- #
    def _refresh_recent_runs(self):
        self.lst_recent.clear()
        try:
            self.lst_recent.addItems(self._recent_runs.items())
        except Exception as e:                        # noqa: BLE001
            self._log(f"Recent run folders not loaded: {e}",
                      Qgis.MessageLevel.Warning)

    def _add_recent_run(self, path):
        if not path:
            return
        try:
            self._recent_runs.add(os.path.normpath(path))
        except Exception:                             # noqa: BLE001
            _LOG.debug("Recent-run entry not stored", exc_info=True)
        self._refresh_recent_runs()

    def _open_recent_run(self, item):
        path = item.text()
        if path and os.path.isdir(path):
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(os.path.normpath(path)))
        else:
            self._log(f"Folder no longer exists: {path}", Qgis.MessageLevel.Warning)

    def _preset_save(self):
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if not ok or not name.strip():
            return
        self.presets.save(name.strip(), self.collect_settings())
        self._refresh_presets()
        self._log(f"Preset saved: {name.strip()}")

    def _preset_load(self):
        item = self.lst_presets.currentItem()
        if item is None:
            return
        s = self.presets.load(item.text())
        if s:
            self.apply_settings(s)
            self._log(f"Preset loaded: {item.text()}")

    def _preset_delete(self):
        item = self.lst_presets.currentItem()
        if item is None:
            return
        if QMessageBox.question(
                self, "Delete preset",
                f"Delete preset '{item.text()}'?") == QMessageBox.StandardButton.Yes:
            self.presets.delete(item.text())
            self._refresh_presets()

    def _preset_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import preset", "", "Preset files (*.json)")
        if path:
            name = self.presets.import_file(path)
            if name:
                self._refresh_presets()
                self._log(f"Preset imported: {name}")

    def _preset_export(self):
        item = self.lst_presets.currentItem()
        if item is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export preset", f"{item.text()}.json",
            "Preset files (*.json)")
        if path:
            self.presets.export_file(item.text(), path)
            self._log(f"Preset exported to {path}")

    # ================================================================== #
    def cleanup(self):
        """Called by the plugin on unload."""
        try:
            s = self.collect_settings()
            self.presets.save_last(s)
            self.settings.save_all_project(s)      # project > last-used
        except Exception:                              # noqa: BLE001
            _LOG.debug("Settings not saved on unload", exc_info=True)
        try:
            QgsProject.instance().readProject.disconnect(
                self._on_project_read)
        except Exception:                              # noqa: BLE001
            _LOG.debug("readProject already disconnected", exc_info=True)
        try:
            self._cursor_timer.stop()
            self._preview_timer.stop()
            self.iface.mapCanvas().xyCoordinates.disconnect(
                self._on_canvas_move)
        except Exception:                              # noqa: BLE001
            _LOG.debug("Canvas signal already disconnected", exc_info=True)
        try:
            app = QgsApplication.instance()
            if app is not None and hasattr(app, "paletteChanged"):
                app.paletteChanged.disconnect(self._on_palette_changed)
        except Exception:                              # noqa: BLE001
            _LOG.debug("paletteChanged already disconnected", exc_info=True)
        self.on_cancel()
        self.overlay.remove()
