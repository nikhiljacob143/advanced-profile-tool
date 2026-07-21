# -*- coding: utf-8 -*-
"""DEM sampling (QGIS boundary): reads raster blocks once per section batch
and interpolates with numpy (sampler_math). Supports per-DEM CRS transform,
vertical offset, NoData handling and cancellation callbacks.
"""
import math
import logging

_LOG = logging.getLogger(__name__)

import numpy as np
from qgis.core import (QgsCoordinateTransform, QgsPointXY, QgsProject,
                       QgsRectangle, QgsRasterLayer)

from ..constants import (INTERP_NEAREST, NODATA_GAP, NODATA_INTERPOLATE)
from . import sampler_math as sm
from .data_models import ProfileLine, ProfileResult

# Cap on a single block read (cells). 64 MB of float64 = 8.4M cells; leave
# headroom for 6 DEMs and numpy temporaries on a 16 GB machine.
MAX_BLOCK_CELLS = 8_000_000


class SamplerError(Exception):
    pass


def layer_pixel_size(layer, calc_crs=None):
    """Approximate pixel size of `layer` in calc-CRS units."""
    px = layer.rasterUnitsPerPixelX()
    py = layer.rasterUnitsPerPixelY()
    size = min(abs(px), abs(py))
    if calc_crs and layer.crs().isValid() and calc_crs.isValid() \
            and layer.crs() != calc_crs:
        try:
            tr = QgsCoordinateTransform(layer.crs(), calc_crs,
                                        QgsProject.instance())
            c = layer.extent().center()
            p1 = tr.transform(c)
            p2 = tr.transform(QgsPointXY(c.x() + px, c.y()))
            size = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
        except Exception:                              # noqa: BLE001
            _LOG.debug("Pixel-size CRS transform failed; using layer "
                       "units value", exc_info=True)
    return size


def suggest_sampling_interval(dem_layers, calc_crs=None):
    """Half the finest pixel size, rounded to a sensible figure, >= 0.1."""
    finest = None
    for lyr in dem_layers:
        s = layer_pixel_size(lyr, calc_crs)
        if s and s > 0:
            finest = s if finest is None else min(finest, s)
    if not finest:
        return 1.0
    raw = max(finest / 2.0, 0.1)
    # round to 1/2/5 series
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * mag * 1.0001:
            return m * mag
    return raw


class DemGridCache:
    """Reads and caches the raster block covering a set of points for one
    DEM, in the DEM's own CRS."""

    def __init__(self, layer, band=1):
        if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
            raise SamplerError(f"Invalid raster layer.")
        self.layer = layer
        self.band = band
        self.provider = layer.dataProvider()
        self.grid = None
        self.origin_x = self.origin_y = 0.0
        self.pixel_x = abs(layer.rasterUnitsPerPixelX())
        self.pixel_y = abs(layer.rasterUnitsPerPixelY())
        self._loaded_extent = None

    def ensure_coverage(self, xs, ys, pad_pixels=4):
        """Load (or extend) the cached block to cover the given points.

        The request is snapped OUTWARD to the raster's native pixel grid so
        the returned block is pixel-aligned and the native resolution is
        preserved exactly (never recomputed from the extent — recomputation
        compounds rounding drift; defect found in live testing)."""
        if len(xs) == 0:
            return
        px, py = self.pixel_x, self.pixel_y
        lext = self.layer.extent()
        xmin = float(np.nanmin(xs)) - pad_pixels * px
        xmax = float(np.nanmax(xs)) + pad_pixels * px
        ymin = float(np.nanmin(ys)) - pad_pixels * py
        ymax = float(np.nanmax(ys)) + pad_pixels * py
        need = QgsRectangle(xmin, ymin, xmax, ymax)
        if self._loaded_extent and self._loaded_extent.contains(need):
            return
        inter = lext.intersect(need)
        if inter.isEmpty():
            self.grid = None
            self._loaded_extent = need
            return
        import math as _m
        x0 = lext.xMinimum() + _m.floor(
            (inter.xMinimum() - lext.xMinimum()) / px) * px
        x1 = lext.xMinimum() + _m.ceil(
            (inter.xMaximum() - lext.xMinimum()) / px) * px
        y0 = lext.yMaximum() - _m.ceil(
            (lext.yMaximum() - inter.yMinimum()) / py) * py
        y1 = lext.yMaximum() - _m.floor(
            (lext.yMaximum() - inter.yMaximum()) / py) * py
        x0, x1 = max(x0, lext.xMinimum()), min(x1, lext.xMaximum())
        y0, y1 = max(y0, lext.yMinimum()), min(y1, lext.yMaximum())
        cols = max(1, int(round((x1 - x0) / px)))
        rows = max(1, int(round((y1 - y0) / py)))
        if cols * rows > MAX_BLOCK_CELLS:
            raise SamplerError(
                f"Requested raster window is too large "
                f"({cols}×{rows} cells at {px:g} m pixels). Reduce the "
                "section widths or process a shorter chainage window.")
        ext = QgsRectangle(x0, y0, x1, y1)
        block = self.provider.block(self.band, ext, cols, rows)
        if block is None or not block.isValid():
            raise SamplerError("Raster block read failed.")
        arr = np.frombuffer(bytes(block.data()),
                            dtype=_block_dtype(block)).astype(np.float64)
        arr = arr.reshape((rows, cols)).copy()
        if block.hasNoDataValue():
            nd = block.noDataValue()
            arr[arr == nd] = np.nan
        self.grid = arr
        self.origin_x = x0
        self.origin_y = y1
        self._loaded_extent = ext

    def sample(self, xs, ys, interp=INTERP_NEAREST):
        if self.grid is None:
            return np.full(len(xs), np.nan)
        cols, rows = sm.world_to_pixel(xs, ys, self.origin_x, self.origin_y,
                                       self.pixel_x, self.pixel_y)
        fn = sm.SAMPLERS.get(interp, sm.sample_nearest)
        return fn(self.grid, cols, rows)


def _block_dtype(block):
    """Map QgsRasterBlock data type to numpy dtype."""
    from qgis.core import Qgis
    m = {Qgis.DataType.Byte: np.uint8,
         Qgis.DataType.Int8: np.int8,
         Qgis.DataType.UInt16: np.uint16,
         Qgis.DataType.Int16: np.int16,
         Qgis.DataType.UInt32: np.uint32,
         Qgis.DataType.Int32: np.int32,
         Qgis.DataType.Float32: np.float32,
         Qgis.DataType.Float64: np.float64}
    dt = block.dataType()
    if dt not in m:
        raise SamplerError(f"Unsupported raster data type: {dt}.")
    return m[dt]


class ProfileSampler:
    """Samples all enabled DEMs along section lines / the alignment.

    Parameters
    ----------
    dem_defs : list[DemDef]
    layer_map : dict[layer_id → QgsRasterLayer]
    calc_crs : QgsCoordinateReferenceSystem of the section geometry
    nodata_mode / nodata_max_gap : NoData policy
    """

    def __init__(self, dem_defs, layer_map, calc_crs,
                 nodata_mode=NODATA_GAP, nodata_max_gap=5.0):
        self.dem_defs = [d for d in dem_defs if d.enabled]
        self.calc_crs = calc_crs
        self.nodata_mode = nodata_mode
        self.nodata_max_gap = nodata_max_gap
        self._entries = []
        for dd in self.dem_defs:
            layer = layer_map.get(dd.layer_id)
            if layer is None or not layer.isValid():
                raise SamplerError(f"DEM layer missing: {dd.name}")
            cache = DemGridCache(layer, dd.band)
            tr = None
            if layer.crs().isValid() and calc_crs.isValid() \
                    and layer.crs() != calc_crs:
                tr = QgsCoordinateTransform(calc_crs, layer.crs(),
                                            QgsProject.instance())
            dd.pixel_size = layer_pixel_size(layer, calc_crs)
            self._entries.append((dd, cache, tr))

    # ------------------------------------------------------------------ #
    def prefetch(self, xs, ys):
        """Try to load one block per DEM covering all given points.

        Silently falls back to per-call loads when the window would exceed
        the memory cap (SamplerError is swallowed here by design)."""
        for dd, cache, tr in self._entries:
            px, py = np.asarray(xs, float), np.asarray(ys, float)
            if tr is not None:
                pts = [tr.transform(QgsPointXY(float(x), float(y)))
                       for x, y in zip(px, py)]
                px = np.array([p.x() for p in pts])
                py = np.array([p.y() for p in pts])
            try:
                cache.ensure_coverage(px, py)
            except SamplerError:
                _LOG.debug("Coverage pre-load failed for a DEM block; "
                           "per-point sampling will report NoData",
                           exc_info=True)

    def sample_points(self, offsets, xs, ys, label="", chainage=0.0,
                      section_id=None):
        """Sample every DEM at prepared world points → ProfileResult."""
        res = ProfileResult(section_id=section_id, label=label,
                            chainage=chainage,
                            offsets=np.asarray(offsets, dtype=np.float64),
                            xs=np.asarray(xs, dtype=np.float64),
                            ys=np.asarray(ys, dtype=np.float64))
        for dd, cache, tr in self._entries:
            px, py = res.xs, res.ys
            if tr is not None:
                pts = [tr.transform(QgsPointXY(float(x), float(y)))
                       for x, y in zip(px, py)]
                px = np.array([p.x() for p in pts])
                py = np.array([p.y() for p in pts])
            cache.ensure_coverage(px, py)
            elev = cache.sample(px, py, dd.interp)
            if dd.v_offset:
                elev = elev + dd.v_offset
            n_nan = int(np.isnan(elev).sum())
            if self.nodata_mode == NODATA_INTERPOLATE and n_nan:
                elev, _ = sm.interpolate_gaps(res.offsets, elev,
                                              self.nodata_max_gap)
                n_nan = int(np.isnan(elev).sum())
            if n_nan:
                res.warnings.append(
                    f"{dd.name}: {n_nan} NoData sample(s) on {label or 'profile'}.")
            res.lines[dd.layer_id] = ProfileLine(
                dem_layer_id=dd.layer_id, offsets=res.offsets,
                elevations=elev, nodata_count=n_nan)
        return res

    def sample_section(self, section, interval):
        """Sample one SectionDef at `interval` spacing along its line.

        Sample positions are SIGNED offsets (negative left) and always
        include both endpoints and offset 0 (the alignment crossing).
        """
        offs = _offset_series(-section.left_width, section.right_width,
                              interval)
        nx, ny = section.normal
        cx, cy = section.center
        # offset o (negative = left) → point = center + normal * (-o),
        # because `normal` points LEFT and left offsets are negative.
        xs = cx + (-offs) * nx
        ys = cy + (-offs) * ny
        return self.sample_points(offs, xs, ys, label=section.label,
                                  chainage=section.chainage,
                                  section_id=section.section_id)

    def sample_line_z(self, alignment, positions_along):
        """Interpolate the alignment's own vertex Z values at raw
        distances along the line.

        ``positions_along`` are RAW geometric distances from the line
        start (not displayed chainages). Uses numpy linear interpolation
        over cum_dist → vertex_z; positions outside the line are clamped
        to the end values. Returns an np.ndarray of elevations (NaN where
        the source Z is NoData), or an all-NaN array when the alignment
        carries no Z (``alignment.vertex_z is None``).
        """
        pos = np.asarray(positions_along, dtype=np.float64)
        if alignment.vertex_z is None or not alignment.cum_dist:
            return np.full(pos.shape, np.nan)
        return np.interp(pos,
                         np.asarray(alignment.cum_dist, dtype=np.float64),
                         np.asarray(alignment.vertex_z, dtype=np.float64))

    def sample_long_section(self, alignment, interval):
        """Sample the alignment itself (long section); positions are
        displayed chainages."""
        from . import geometry_math as gm
        L = alignment.length
        n = max(2, int(math.floor(L / max(interval, 1e-6))) + 1)
        dists = np.minimum(np.arange(n, dtype=np.float64) * interval, L)
        if dists[-1] < L:
            dists = np.append(dists, L)
        # include vertices for exactness at bends
        dists = np.unique(np.concatenate(
            [dists, np.asarray(alignment.cum_dist)]))
        pts = [gm.point_at(alignment.vertices, alignment.cum_dist, float(d))
               for d in dists]
        xs = np.array([p[0] for p in pts])
        ys = np.array([p[1] for p in pts])
        chain = dists + alignment.start_chainage
        return self.sample_points(chain, xs, ys,
                                  label=f"Long section — {alignment.name}",
                                  chainage=alignment.start_chainage,
                                  section_id=None)


def _offset_series(o_min, o_max, interval):
    """Offsets from o_min to o_max at `interval`, always including 0 and
    both ends."""
    interval = max(float(interval), 1e-6)
    neg = np.arange(0.0, -o_min + 1e-9, interval)
    pos = np.arange(0.0, o_max + 1e-9, interval)
    offs = np.unique(np.concatenate([-neg, pos,
                                     np.array([o_min, o_max, 0.0])]))
    return offs[(offs >= o_min - 1e-9) & (offs <= o_max + 1e-9)]
