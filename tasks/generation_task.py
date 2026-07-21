# -*- coding: utf-8 -*-
"""Background task: generate sections and sample all DEM profiles.

Copyright (C) 2026 Nikhil Jacob — GPL v2 or later.
"""
import time
import traceback

import numpy as np

from qgis.core import QgsTask

from ..constants import LINE_Z_SURFACE_ID
from ..core.data_models import ProfileLine
from ..core.raster_sampler import ProfileSampler, SamplerError


class GenerateAndSampleTask(QgsTask):
    """Runs sampling off the UI thread.

    Inputs are prepared on the main thread (sections built synchronously —
    they are cheap); the expensive raster sampling happens here.

    When ``use_line_z`` is True and the alignment carries per-vertex Z
    (``alignment.vertex_z`` is not None), every ProfileResult gains an
    extra ProfileLine under the pseudo-DEM id
    ``constants.LINE_Z_SURFACE_ID`` ("__alignment_z__"):

    * cross-sections — the alignment Z interpolated at the section's raw
      chainage, constant across all offsets;
    * long section — the alignment Z interpolated along the line at each
      sample position.

    The dock adds the matching DemDef-like legend entry for this id.

    Results: .profiles (list[ProfileResult] per section), .long_profile,
    .error (str or None), .elapsed_s.
    """

    def __init__(self, description, alignment, sections, dem_defs,
                 layer_map, calc_crs, sampling_interval,
                 nodata_mode, nodata_max_gap, sample_long=True,
                 use_line_z=False):
        super().__init__(description, QgsTask.Flag.CanCancel)
        self.alignment = alignment
        self.sections = sections
        self.dem_defs = dem_defs
        self.layer_map = layer_map
        self.calc_crs = calc_crs
        self.sampling_interval = float(sampling_interval)
        self.nodata_mode = nodata_mode
        self.nodata_max_gap = nodata_max_gap
        self.sample_long = sample_long
        self.use_line_z = bool(use_line_z)
        self.profiles = []
        self.long_profile = None
        self.warnings = []
        self.error = None
        self.elapsed_s = 0.0

    def _line_z_available(self):
        return self.use_line_z and \
            getattr(self.alignment, "vertex_z", None) is not None

    def _append_section_line_z(self, sampler, sec, res):
        """Attach the alignment-Z pseudo-surface to a cross-section
        result (constant elevation = alignment Z at the section's raw
        chainage)."""
        z = sampler.sample_line_z(self.alignment,
                                  np.array([float(sec.distance)]))
        z0 = float(z[0])
        n = len(res.offsets) if res.offsets is not None else 0
        elev = np.full(n, z0, dtype=np.float64)
        nodata = int(np.isnan(elev).sum())
        res.lines[LINE_Z_SURFACE_ID] = ProfileLine(
            dem_layer_id=LINE_Z_SURFACE_ID, offsets=res.offsets,
            elevations=elev, nodata_count=nodata)

    def _append_long_line_z(self, sampler, res):
        """Attach the alignment-Z pseudo-surface along the long section
        (positions in res.offsets are displayed chainages)."""
        raw = np.asarray(res.offsets, dtype=np.float64) \
            - float(self.alignment.start_chainage)
        elev = sampler.sample_line_z(self.alignment, raw)
        nodata = int(np.isnan(elev).sum())
        res.lines[LINE_Z_SURFACE_ID] = ProfileLine(
            dem_layer_id=LINE_Z_SURFACE_ID, offsets=res.offsets,
            elevations=elev, nodata_count=nodata)

    def run(self):
        t0 = time.time()
        try:
            sampler = ProfileSampler(
                self.dem_defs, self.layer_map, self.calc_crs,
                nodata_mode=self.nodata_mode,
                nodata_max_gap=self.nodata_max_gap)
            # one bulk block read per DEM when it fits in memory
            corner_x, corner_y = [], []
            for sec in self.sections:
                for p in (sec.left_point, sec.right_point):
                    corner_x.append(p[0])
                    corner_y.append(p[1])
            for x, y in self.alignment.vertices:
                corner_x.append(x)
                corner_y.append(y)
            sampler.prefetch(corner_x, corner_y)
            line_z = self._line_z_available()
            n = len(self.sections) + (1 if self.sample_long else 0)
            done = 0
            for sec in self.sections:
                if self.isCanceled():
                    self.error = "Cancelled by user."
                    return False
                res = sampler.sample_section(sec, self.sampling_interval)
                if line_z:
                    self._append_section_line_z(sampler, sec, res)
                self.profiles.append(res)
                self.warnings.extend(res.warnings)
                done += 1
                if done % 5 == 0 or done == n:
                    self.setProgress(100.0 * done / max(n, 1))
            if self.sample_long:
                if self.isCanceled():
                    self.error = "Cancelled by user."
                    return False
                self.long_profile = sampler.sample_long_section(
                    self.alignment, self.sampling_interval)
                if line_z:
                    self._append_long_line_z(sampler, self.long_profile)
                self.warnings.extend(self.long_profile.warnings)
                self.setProgress(100.0)
            self.elapsed_s = time.time() - t0
            return True
        except SamplerError as e:
            self.error = str(e)
            return False
        except Exception:                              # noqa: BLE001
            self.error = traceback.format_exc(limit=8)
            return False
