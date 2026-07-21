# -*- coding: utf-8 -*-
"""Plugin-wide constants and factory defaults. Pure Python (no qgis imports)."""

PLUGIN_NAME = "Advanced Profile Tool"
PLUGIN_PACKAGE = "advanced_profile_tool"
PLUGIN_VERSION = "1.2.0"

# ---- chainage formats -------------------------------------------------------
CHAINAGE_PLAIN = "plain"          # 1240.0
CHAINAGE_STATION = "0+000"        # 1+240.00
CHAINAGE_CH = "CH 0+000"          # CH 1+240.00
CHAINAGE_STA = "STA 0+000"        # STA 1+240.00
CHAINAGE_FORMATS = [CHAINAGE_STATION, CHAINAGE_CH, CHAINAGE_STA, CHAINAGE_PLAIN]

# ---- offset display conventions --------------------------------------------
OFFSET_NEG_LEFT = "neg_left"      # negative left / positive right (default)
OFFSET_POS_BOTH = "pos_both"

# ---- tangent methods ---------------------------------------------------------
TANGENT_LOCAL = "local"
TANGENT_AVERAGED = "averaged"
TANGENT_SMOOTHED = "smoothed"
TANGENT_METHODS = [TANGENT_LOCAL, TANGENT_AVERAGED, TANGENT_SMOOTHED]

# ---- vertex handling (section direction exactly at alignment vertices) -------
VERTEX_BISECTOR = "bisector"
VERTEX_INCOMING = "incoming"
VERTEX_OUTGOING = "outgoing"
VERTEX_MODES = [VERTEX_BISECTOR, VERTEX_INCOMING, VERTEX_OUTGOING]

# ---- section generation modes ------------------------------------------------
MODE_INTERVAL = "interval"
MODE_COUNT = "count"
MODE_LIST = "list"

# ---- endpoint inclusion ------------------------------------------------------
INCLUDE_BOTH = "both"
INCLUDE_START = "start"
INCLUDE_END = "end"
INCLUDE_NONE = "none"

# ---- DEM interpolation -------------------------------------------------------
INTERP_NEAREST = "nearest"
INTERP_BILINEAR = "bilinear"
INTERP_CUBIC = "cubic"
INTERP_METHODS = [INTERP_NEAREST, INTERP_BILINEAR, INTERP_CUBIC]

# ---- NoData handling ---------------------------------------------------------
NODATA_GAP = "gap"                # warn and leave gaps (default)
NODATA_INTERPOLATE = "interpolate"  # bridge gaps shorter than max_gap

# ---- multipart handling ------------------------------------------------------
MULTIPART_SEPARATE = "separate"
MULTIPART_JOIN_TOL = "join"
MULTIPART_REJECT = "reject"

# ---- factory defaults (confirmed by user questionnaire, 2026-07-15) ----------
DEFAULTS = {
    "units": "m",
    "chainage_format": CHAINAGE_STATION,
    "offset_convention": OFFSET_NEG_LEFT,
    "section_interval": 20.0,
    "left_width": 25.0,
    "right_width": 25.0,
    "sampling_interval": 0.0,          # 0 = auto from DEM pixel size
    "include_endpoints": INCLUDE_BOTH,
    "tangent_method": TANGENT_LOCAL,
    "tangent_avg_distance": 10.0,
    "interp_method": INTERP_BILINEAR,
    "nodata_mode": NODATA_GAP,
    "nodata_max_gap": 5.0,
    "section_prefix": "XS",
    "section_start_number": 1,
    "section_number_padding": 2,
    "major_every": 5,
    "line_color": "#D32F2F",
    "line_color_major": "#7B1FA2",
    "line_width_mm": 0.5,
    "line_width_major_mm": 0.8,
    "line_style": "solid",
    "label_format": "{prefix}{number} {chainage}",
    "label_position": "left",
    "vertical_exaggeration": 10.0,
    "page_size": "A3",
    "page_landscape": True,
    "gpkg_name": "advanced_profile_outputs.gpkg",
    "timestamped_runs": True,
    "output_subfolders": True,
    "multipart_mode": MULTIPART_JOIN_TOL,
    "multipart_join_tol": 0.01,
    "decimals_chainage": 2,
    "decimals_elevation": 3,
    "decimals_offset": 2,
    # ---- v1.2.0 additions (100%-compliance round) -----------------------
    "width_mode": "separate",          # separate | equal | total
    "total_width": 50.0,
    "vertex_handling": "bisector",     # bisector | incoming | outgoing
    "min_segment_length": 1e-6,
    "station_equations": [],           # [[raw_chainage, new_chainage], ...]
    "use_line_z": False,               # plot alignment Z as a surface
    "excel_per_section": False,
    "excel_thumbnails": False,
    "export_dpi": 200,
    "csv_delimiter": ",",
    "convention_fill_above": True,     # fill = comparison above reference
    "vector_format": "gpkg",           # gpkg | shp | geojson
    "output_beside_project": False,
    "label_font_family": "",           # empty = default font
    "label_buffer_mm": 0.8,
    "line_style_major": "solid",
    "dxf_text_height": 0.5,
    "dxf_sheet_cols": 4,
    "dxf_tile_gap": 50.0,
    "dxf_layer_prefix": "APT_",
    "dxf_vertical_exaggeration": 1.0,
}

LINE_Z_SURFACE_ID = "__alignment_z__"   # pseudo-DEM id for line Z profiles

# ---- limits / thresholds -----------------------------------------------------
MAX_PLOT_POINTS_PER_LINE = 4000      # decimate above this
MIN_SEGMENT_LENGTH = 1e-6
CHAINAGE_MERGE_TOL = 1e-4            # duplicate chainage tolerance (m)
GEOGRAPHIC_CRS_WARNING = (
    "The alignment CRS is geographic (degrees). Distances would be computed "
    "in degrees. Select a projected calculation CRS."
)

DXF_LAYER_PREFIX = "APT_"
SETTINGS_GROUP = "advanced_profile_tool"
PROJECT_SCOPE = "AdvancedProfileTool"
LOG_TAG = "AdvancedProfileTool"
