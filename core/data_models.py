# -*- coding: utf-8 -*-
"""Plain data containers passed between engines, UI, tasks and exporters.

These deliberately avoid QGIS types in their *stored* fields wherever
practical (geometry is kept as coordinate arrays / WKT) so that the maths
engines and the test-suite can run without a QGIS instance. Boundary code
(alignment_engine, raster_sampler, vector_exporter, map tools) converts to
and from QGIS objects.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class AlignmentDef:
    """A resolved alignment ready for section generation.

    vertices: list of (x, y) in the calculation CRS, single-part, cleaned.
    cum_dist: cumulative distance at each vertex (same length as vertices).
    vertex_z: per-vertex Z values captured from a 3D source geometry
    (same length as vertices; NaN = NoData) or None for 2D sources —
    enables plotting the alignment Z as a pseudo-surface.
    """
    name: str = "Alignment"
    layer_id: str = ""
    feature_id: int = -1
    vertices: List[Tuple[float, float]] = field(default_factory=list)
    cum_dist: List[float] = field(default_factory=list)
    crs_authid: str = ""
    start_chainage: float = 0.0        # displayed chainage at geometric start
    reversed: bool = False
    part_index: int = 0                # source part for multipart handling
    vertex_z: Optional[List[float]] = None   # Z per vertex (NaN = NoData)

    @property
    def length(self) -> float:
        return self.cum_dist[-1] if self.cum_dist else 0.0

    @property
    def end_chainage(self) -> float:
        return self.start_chainage + self.length


@dataclass
class SectionDef:
    """One cross-section line."""
    section_id: int = 0
    label: str = ""
    chainage: float = 0.0              # displayed chainage
    distance: float = 0.0              # geometric distance along alignment
    center: Tuple[float, float] = (0.0, 0.0)
    tangent: Tuple[float, float] = (1.0, 0.0)   # unit vector, +chainage dir
    normal: Tuple[float, float] = (0.0, 1.0)    # unit vector, LEFT side
    left_width: float = 25.0
    right_width: float = 25.0
    angle_offset_deg: float = 0.0
    is_major: bool = False
    source: str = "generated"          # generated | manual | vertex | list
    warnings: List[str] = field(default_factory=list)

    @property
    def left_point(self) -> Tuple[float, float]:
        return (self.center[0] + self.normal[0] * self.left_width,
                self.center[1] + self.normal[1] * self.left_width)

    @property
    def right_point(self) -> Tuple[float, float]:
        return (self.center[0] - self.normal[0] * self.right_width,
                self.center[1] - self.normal[1] * self.right_width)

    @property
    def total_width(self) -> float:
        return self.left_width + self.right_width


@dataclass
class DemDef:
    """One DEM surface with per-surface settings."""
    layer_id: str = ""
    name: str = ""
    band: int = 1
    interp: str = "bilinear"
    v_offset: float = 0.0
    v_units: str = "m"
    datum_note: str = ""
    color: str = "#1976D2"
    line_style: str = "solid"
    line_width: float = 1.2
    enabled: bool = True
    is_reference: bool = False
    pixel_size: float = 0.0            # filled by sampler (calc CRS units)


@dataclass
class ProfileLine:
    """Sampled elevations for ONE DEM along one section / long section.

    Arrays are numpy float64; elevation uses NaN for NoData.
    """
    dem_layer_id: str = ""
    offsets: object = None             # np.ndarray — offset (XS) or chainage (LS)
    elevations: object = None          # np.ndarray with NaN gaps
    nodata_count: int = 0


@dataclass
class ProfileResult:
    """All DEM samples for one section (or the long section when
    section_id is None)."""
    section_id: Optional[int] = None
    label: str = ""
    chainage: float = 0.0
    offsets: object = None             # np.ndarray sample positions
    xs: object = None                  # np.ndarray world X per sample
    ys: object = None                  # np.ndarray world Y per sample
    lines: Dict[str, ProfileLine] = field(default_factory=dict)  # by layer_id
    warnings: List[str] = field(default_factory=list)


@dataclass
class SectionComparison:
    """Cut/fill areas for one section between reference and comparison DEM."""
    section_id: int = 0
    label: str = ""
    chainage: float = 0.0
    cut_area: float = 0.0              # comparison below reference
    fill_area: float = 0.0             # comparison above reference
    gap_length: float = 0.0            # offset length excluded by NoData
    valid: bool = True

    @property
    def net_area(self) -> float:
        return self.fill_area - self.cut_area


@dataclass
class VolumeRow:
    """Average-end-area volume between two consecutive valid sections."""
    from_id: int = 0
    to_id: int = 0
    from_chainage: float = 0.0
    to_chainage: float = 0.0
    length: float = 0.0
    cut_volume: float = 0.0
    fill_volume: float = 0.0
    cum_cut: float = 0.0
    cum_fill: float = 0.0

    @property
    def net_volume(self) -> float:
        return self.fill_volume - self.cut_volume

    @property
    def cum_net(self) -> float:
        return self.cum_fill - self.cum_cut


@dataclass
class RunParameters:
    """Everything needed to reproduce a generation/sampling run."""
    alignment: Optional[AlignmentDef] = None
    sections: List[SectionDef] = field(default_factory=list)
    dems: List[DemDef] = field(default_factory=list)
    sampling_interval: float = 1.0
    settings: dict = field(default_factory=dict)

    def enabled_dems(self) -> List[DemDef]:
        return [d for d in self.dems if d.enabled]

    def reference_dem(self) -> Optional[DemDef]:
        for d in self.dems:
            if d.enabled and d.is_reference:
                return d
        return None
