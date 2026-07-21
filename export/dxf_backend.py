# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Thin DXF writing abstraction over the available backend.

Two interchangeable implementations of the same small interface:

* the vendored ``dxfwrite`` package (DXF R12, always available); and
* ``ezdxf`` (DXF R2010) when it happens to be installed in the Python
  environment — selected automatically at import time.

The QGIS 3.44 bundled environment does not ship ezdxf, so the vendored
writer is the normal path; the ezdxf branch exists for user environments
where it is present.

No qgis imports — the module is testable headless.
"""
import logging

logger = logging.getLogger(__name__)

try:  # optional, preferred when present (newer DXF version)
    import ezdxf  # noqa: F401
    _HAS_EZDXF = True
except ImportError:
    _HAS_EZDXF = False

from ..vendor import dxfwrite
from ..vendor.dxfwrite import const as _dxfconst

__all__ = ["DxfDoc", "hex_to_aci", "ACI_COLOURS"]

#: AutoCAD colour index → representative RGB, used for nearest matching.
#: Index 7 is white on dark backgrounds / black on light — both map to it.
ACI_COLOURS = (
    (1, (255, 0, 0)),        # red
    (2, (255, 255, 0)),      # yellow
    (3, (0, 255, 0)),        # green
    (4, (0, 255, 255)),      # cyan
    (5, (0, 0, 255)),        # blue
    (6, (255, 0, 255)),      # magenta
    (7, (255, 255, 255)),    # white
    (7, (0, 0, 0)),          # black (also index 7)
    (8, (128, 128, 128)),    # grey
)


def hex_to_aci(hex_colour):
    """Map a ``#RRGGBB`` hex colour to the nearest basic AutoCAD colour
    index in {1 red, 2 yellow, 3 green, 4 cyan, 5 blue, 6 magenta,
    7 white/black, 8 grey}.

    Invalid or missing input returns 7 (white/black, ByLayer-friendly).
    """
    if not hex_colour:
        return 7
    s = str(hex_colour).strip().lstrip("#")
    if len(s) == 3:                       # short form, e.g. #f00
        s = "".join(ch * 2 for ch in s)
    if len(s) < 6:
        return 7
    try:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
    except ValueError:
        return 7
    best_index, best_dist = 7, float("inf")
    for aci, (cr, cg, cb) in ACI_COLOURS:
        dist = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if dist < best_dist:
            best_index, best_dist = aci, dist
    return best_index


class DxfDoc:
    """One DXF document with a minimal drawing interface.

    Instantiating ``DxfDoc(path)`` returns the ezdxf-backed implementation
    when ezdxf is importable, otherwise the vendored dxfwrite (R12)
    implementation. Both expose:

    * ``add_layer(name, color_index)``
    * ``polyline(points2d, layer, closed=False)``
    * ``text(txt, xy, height, layer, halign="LEFT", rotation=0.0)``
    * ``line(p1, p2, layer)``
    * ``save()``
    """

    def __new__(cls, path):
        if cls is DxfDoc:
            impl = _EzdxfDoc if _HAS_EZDXF else _DxfwriteDoc
            return super().__new__(impl)
        return super().__new__(cls)

    def __init__(self, path):
        self.path = str(path)

    # interface stubs — implemented by the backends
    def add_layer(self, name, color_index=7):
        """Declare a layer with an AutoCAD colour index."""
        raise NotImplementedError

    def polyline(self, points2d, layer, closed=False):
        """Add a 2D polyline (list of (x, y)) on ``layer``."""
        raise NotImplementedError

    def text(self, txt, xy, height, layer, halign="LEFT", rotation=0.0):
        """Add a TEXT entity anchored at ``xy``."""
        raise NotImplementedError

    def line(self, p1, p2, layer):
        """Add a single LINE entity."""
        raise NotImplementedError

    def save(self):
        """Write the document to ``self.path``."""
        raise NotImplementedError


class _DxfwriteDoc(DxfDoc):
    """DXF R12 implementation using the vendored dxfwrite package.

    Confirmed vendored API: ``dxfwrite.DXFEngine.drawing(name)`` returns a
    ``Drawing`` with ``add_layer(name, color=int)``, ``add(entity)`` and
    ``save()``; entities come from the ``DXFEngine`` static factory
    (``polyline(points, layer=..., flags=...)``, ``text(text, insert=...,
    height=..., rotation=..., halign=..., alignpoint=...)``,
    ``line(start=..., end=..., layer=...)``).
    """

    _HALIGN = {"LEFT": _dxfconst.LEFT,
               "CENTER": _dxfconst.CENTER,
               "RIGHT": _dxfconst.RIGHT}

    def __init__(self, path):
        super().__init__(path)
        self._drawing = dxfwrite.DXFEngine.drawing(self.path)

    def add_layer(self, name, color_index=7):
        self._drawing.add_layer(name, color=int(color_index))

    def polyline(self, points2d, layer, closed=False):
        pts = [(float(x), float(y)) for x, y in points2d]
        if len(pts) < 2:
            return
        flags = _dxfconst.POLYLINE_CLOSED if closed else 0
        self._drawing.add(dxfwrite.DXFEngine.polyline(
            pts, layer=layer, flags=flags))

    def text(self, txt, xy, height, layer, halign="LEFT", rotation=0.0):
        code = self._HALIGN.get(str(halign).upper(), _dxfconst.LEFT)
        kwargs = {"insert": (float(xy[0]), float(xy[1])),
                  "height": float(height),
                  "rotation": float(rotation),
                  "layer": layer}
        if code != _dxfconst.LEFT:
            # non-default justification requires an alignment point
            kwargs["halign"] = code
            kwargs["alignpoint"] = kwargs["insert"]
        self._drawing.add(dxfwrite.DXFEngine.text(str(txt), **kwargs))

    def line(self, p1, p2, layer):
        self._drawing.add(dxfwrite.DXFEngine.line(
            start=(float(p1[0]), float(p1[1])),
            end=(float(p2[0]), float(p2[1])),
            layer=layer))

    def save(self):
        self._drawing.save()


class _EzdxfDoc(DxfDoc):
    """DXF R2010 implementation using ezdxf when it is installed."""

    _HALIGN = {"LEFT": 0, "CENTER": 1, "RIGHT": 2}

    def __init__(self, path):
        super().__init__(path)
        self._doc = ezdxf.new("R2010")
        self._msp = self._doc.modelspace()

    def add_layer(self, name, color_index=7):
        if name not in self._doc.layers:
            self._doc.layers.add(name, color=int(color_index))

    def polyline(self, points2d, layer, closed=False):
        pts = [(float(x), float(y)) for x, y in points2d]
        if len(pts) < 2:
            return
        pl = self._msp.add_lwpolyline(pts, dxfattribs={"layer": layer})
        if closed:
            pl.close(True)

    def text(self, txt, xy, height, layer, halign="LEFT", rotation=0.0):
        attribs = {"layer": layer, "height": float(height),
                   "rotation": float(rotation),
                   "insert": (float(xy[0]), float(xy[1]))}
        code = self._HALIGN.get(str(halign).upper(), 0)
        if code:
            attribs["halign"] = code
            attribs["align_point"] = attribs["insert"]
        self._msp.add_text(str(txt), dxfattribs=attribs)

    def line(self, p1, p2, layer):
        self._msp.add_line((float(p1[0]), float(p1[1])),
                           (float(p2[0]), float(p2[1])),
                           dxfattribs={"layer": layer})

    def save(self):
        self._doc.saveas(self.path)
