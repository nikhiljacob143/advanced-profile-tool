# -*- coding: utf-8 -*-
"""Interactive matplotlib profile canvas (cross-section and long-section).

Copyright (C) 2026 Nikhil Jacob — GPL v2 or later.
"""
import logging
import datetime

_LOG = logging.getLogger(__name__)

import numpy as np

import matplotlib
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from matplotlib.backends.backend_qt5agg import (FigureCanvasQTAgg,
                                                NavigationToolbar2QT)

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QVBoxLayout, QWidget, QApplication

from ..constants import MAX_PLOT_POINTS_PER_LINE
from ..core.units import format_chainage
from ..styling.profile_style_manager import dem_style, qgis_theme_colors


def decimate(x, y, max_points=MAX_PLOT_POINTS_PER_LINE):
    """Min/max envelope decimation preserving peaks and NoData gaps."""
    n = len(x)
    if n <= max_points:
        return x, y
    bins = max_points // 2
    edges = np.linspace(0, n, bins + 1).astype(int)
    xs, ys = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        seg = y[a:b]
        if np.all(np.isnan(seg)):
            xs.append(x[(a + b) // 2])
            ys.append(np.nan)
            continue
        imin = a + int(np.nanargmin(seg))
        imax = a + int(np.nanargmax(seg))
        for i in sorted((imin, imax)):
            xs.append(x[i])
            ys.append(y[i])
    return np.asarray(xs), np.asarray(ys)


class ProfilePlotWidget(QWidget):
    """Matplotlib canvas with hover readout, markers, VE control, datum
    line and cut/fill shading. Emits hover_moved(float x, dict readout)
    and point_picked(float x, float y)."""

    hover_moved = pyqtSignal(float, dict)
    point_picked = pyqtSignal(float, float)

    def __init__(self, parent=None, dark=False):
        super().__init__(parent)
        self.fig = Figure(figsize=(7, 3.2), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setIconSize(self.toolbar.iconSize() * 0.8)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.canvas)
        self.ax = self.fig.add_subplot(111)
        self._dark = dark
        self._profile = None
        self._dem_defs = []
        self._mode = "xs"                      # xs | ls
        self._ve = 10.0
        self._datum = None
        self._shade_pair = None                # (ref_id, cmp_id)
        self._diff_pair = None                 # (ref_id, cmp_id) Δz mode
        # markers are remembered PER PROFILE: keyed by section_id (or
        # 'ls' for the long section); the active key follows show_profile.
        self._markers_by_key = {}              # key → [(x, y, annotation)]
        self._active_key = None
        self._chfmt = "0+000"
        self._context_alignment = ""           # annotation under the axes
        self._context_artist = None
        self._series = {}                      # dem_id → (x, y) plotted
        self._cursor_line = None               # map→plot linkage artist
        self.canvas.mpl_connect("motion_notify_event", self._on_move)
        self.canvas.mpl_connect("button_press_event", self._on_click)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)
        self.redraw()

    # ------------------------------------------------------------------ #
    def set_theme(self, dark):
        self._dark = bool(dark)
        self.redraw()

    def set_vertical_exaggeration(self, ve):
        self._ve = max(float(ve), 0.01)
        self.redraw()

    def set_datum(self, elevation_or_none):
        self._datum = elevation_or_none
        self.redraw()

    def set_shading(self, ref_id_cmp_id_or_none):
        self._shade_pair = ref_id_cmp_id_or_none
        self.redraw()

    def set_difference_pair(self, pair_or_none):
        """(ref_id, cmp_id) → plot the difference profile Δz (cmp − ref)
        instead of the surfaces; None restores the surface view."""
        self._diff_pair = pair_or_none
        self.redraw()

    def set_chainage_format(self, fmt):
        self._chfmt = fmt

    def set_context(self, alignment_name):
        """Set the alignment name shown in the small right-aligned
        context line under the axes (with the chainage/'long section'
        note and today's date). The dock calls this once per run."""
        self._context_alignment = str(alignment_name or "")
        self.redraw()

    def clear_markers(self):
        """Clear the markers of the ACTIVE profile only (markers are
        remembered per section / long section)."""
        self._markers_by_key.pop(self._active_key, None)
        self.redraw()

    def _profile_key(self, profile, mode):
        if mode == "ls":
            return "ls"
        if profile is None:
            return None
        return profile.section_id

    # ------------------------------------------------------------------ #
    def show_profile(self, profile, dem_defs, mode="xs"):
        """Display a ProfileResult. mode 'xs' (offset axis) or 'ls'
        (chainage axis). Switches the active per-profile marker list."""
        self._profile = profile
        self._dem_defs = [d for d in dem_defs if d.enabled]
        self._mode = mode
        self._active_key = self._profile_key(profile, mode)
        self.redraw()

    def show_cursor(self, x_or_none):
        """Light-weight vertical cursor for map→plot linkage; None hides
        it. Avoids a full redraw — only the artist is updated."""
        if x_or_none is None:
            if self._cursor_line is not None and \
                    self._cursor_line.get_visible():
                self._cursor_line.set_visible(False)
                self.canvas.draw_idle()
            return
        x = float(x_or_none)
        if self._cursor_line is None or \
                self._cursor_line.axes is not self.ax:
            self._cursor_line = self.ax.axvline(
                x, color="#F57C00", linewidth=1.2, linestyle=":",
                zorder=10)
        else:
            self._cursor_line.set_xdata([x, x])
            self._cursor_line.set_visible(True)
        self.canvas.draw_idle()

    def redraw(self):
        colors = qgis_theme_colors(self._dark)
        self.fig.set_facecolor(colors["background"])
        self.ax.clear()
        self._cursor_line = None               # destroyed by ax.clear()
        self.ax.set_facecolor(colors["background"])
        for spine in self.ax.spines.values():
            spine.set_color(colors["foreground"])
        self.ax.tick_params(colors=colors["foreground"], labelsize=8)
        self.ax.grid(True, which="major", color=colors["grid"],
                     linewidth=0.6, alpha=0.9)
        self.ax.grid(True, which="minor", color=colors["grid"],
                     linewidth=0.3, alpha=0.5)
        self.ax.minorticks_on()
        self._series = {}
        p = self._profile
        if p is None or p.offsets is None or not len(p.offsets):
            self.ax.set_title("No profile loaded",
                              color=colors["foreground"], fontsize=9)
            self.canvas.draw_idle()
            return
        x_all = np.asarray(p.offsets, dtype=float)
        # difference (Δz) mode: single line (cmp − ref), zero axis,
        # no surface lines, shading or datum
        diff_mode = False
        if self._diff_pair:
            ref_id, cmp_id = self._diff_pair
            rl = p.lines.get(ref_id)
            cl = p.lines.get(cmp_id)
            if rl is not None and cl is not None \
                    and rl.elevations is not None \
                    and cl.elevations is not None:
                diff_mode = True
                dz = (np.asarray(cl.elevations, dtype=float)
                      - np.asarray(rl.elevations, dtype=float))
                xd, yd = decimate(x_all, dz)
                self.ax.plot(xd, yd, label="Δz (cmp − ref)",
                             color="#D32F2F", linewidth=1.2)
                self.ax.axhline(0.0, color=colors["foreground"],
                                linewidth=0.7, alpha=0.6)
                self._series["__diff__"] = (x_all, dz)
        if not diff_mode:
            for dd in self._dem_defs:
                line = p.lines.get(dd.layer_id)
                if line is None or line.elevations is None:
                    continue
                y = np.asarray(line.elevations, dtype=float)
                xd, yd = decimate(x_all, y)
                st = dem_style(dd)
                self.ax.plot(xd, yd, label=dd.name, color=st["color"],
                             linestyle=st["linestyle"],
                             linewidth=st["linewidth"])
                self._series[dd.layer_id] = (x_all, y)
        # cut/fill shading between a surface pair
        if self._shade_pair and not diff_mode:
            ref_id, cmp_id = self._shade_pair
            r = self._series.get(ref_id)
            c = self._series.get(cmp_id)
            if r is not None and c is not None:
                yr, yc = r[1], c[1]
                ok = ~(np.isnan(yr) | np.isnan(yc))
                self.ax.fill_between(x_all, yr, yc,
                                     where=ok & (yc >= yr),
                                     color="#2E7D32", alpha=0.25,
                                     linewidth=0, label="Fill")
                self.ax.fill_between(x_all, yr, yc,
                                     where=ok & (yc < yr),
                                     color="#C62828", alpha=0.25,
                                     linewidth=0, label="Cut")
        if self._datum is not None and not diff_mode:
            self.ax.axhline(float(self._datum), color="#0288D1",
                            linewidth=0.9, linestyle="--")
            self.ax.annotate(f"Datum RL {self._datum:.2f}",
                             xy=(0.01, self._datum),
                             xycoords=("axes fraction", "data"),
                             fontsize=7, color="#0288D1",
                             va="bottom")
        for mx, my, note in self._markers_by_key.get(self._active_key, []):
            self.ax.plot([mx], [my], marker="o", markersize=4,
                         color="#F57C00")
            self.ax.annotate(note, xy=(mx, my), fontsize=7,
                             color=colors["foreground"],
                             xytext=(4, 4), textcoords="offset points")
        if self._mode == "xs":
            self.ax.set_xlabel("Offset (m)  —  left negative / right "
                               "positive", fontsize=8,
                               color=colors["foreground"])
            title = p.label or "Cross-section"
            self.ax.axvline(0.0, color=colors["foreground"],
                            linewidth=0.5, alpha=0.4)
        else:
            self.ax.set_xlabel("Chainage", fontsize=8,
                               color=colors["foreground"])
            title = p.label or "Long section"
            # tick labels in the configured chainage convention
            self.ax.xaxis.set_major_formatter(FuncFormatter(
                lambda v, _pos: format_chainage(v, self._chfmt, 0)))
        self.ax.set_ylabel("Δz (m)" if diff_mode else "Elevation (m)",
                           fontsize=8, color=colors["foreground"])
        # VE via the axes-box aspect: y drawn VE× larger than x. The box is
        # clamped to sane proportions; when clamping bites, the EFFECTIVE
        # VE is reported in the title so the drawing never lies.
        ve_eff = self._ve
        try:
            x0, x1 = self.ax.get_xlim()
            y0, y1 = self.ax.get_ylim()
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            if dx > 0 and dy > 0:
                box = min(max(dy * self._ve / dx, 0.10), 1.8)
                self.ax.set_aspect("auto")
                self.ax.set_box_aspect(box)
                ve_eff = box * dx / dy
        except Exception:
            self.ax.set_aspect("auto")
        ve_note = "true scale" if abs(ve_eff - 1.0) < 0.05 \
            else f"VE {ve_eff:.1f}×"
        self.ax.set_title(f"{title}   [{ve_note}]",
                          color=colors["foreground"], fontsize=9)
        leg = self.ax.legend(fontsize=7, loc="best")
        if leg is not None:
            leg.get_frame().set_alpha(0.6)
        self._draw_context_line(colors)
        self.canvas.draw_idle()

    def _draw_context_line(self, colors):
        """Small right-aligned context annotation under the axes:
        "<alignment> | <chainage or 'long section'> | <today ISO>"."""
        # remove every previous context annotation on THIS figure (they
        # are tagged with a gid — single-artist tracking leaked copies
        # across render_figure()'s figure swaps; observed live)
        for txt in list(self.fig.texts):
            if txt.get_gid() == "apt_context":
                try:
                    txt.remove()
                except (ValueError, NotImplementedError):
                    _LOG.debug("Context text already detached from figure",
                               exc_info=True)
        self._context_artist = None
        p = self._profile
        if p is None:
            return
        if self._mode == "ls":
            where = "long section"
        else:
            where = format_chainage(p.chainage, self._chfmt)
        parts = [s for s in (self._context_alignment, where,
                             datetime.date.today().isoformat()) if s]
        # top-right corner: clear of the x-axis labels and the centred
        # title (overlap with tick labels was observed in live testing)
        self._context_artist = self.fig.text(
            0.99, 0.99, " | ".join(parts), ha="right", va="top",
            fontsize=6, color=colors["foreground"], alpha=0.8,
            gid="apt_context")

    # ------------------------------------------------------------------ #
    def _on_move(self, event):
        if event.inaxes != self.ax or self._profile is None:
            return
        x = float(event.xdata)
        readout = {}
        for dd in self._dem_defs:
            s = self._series.get(dd.layer_id)
            if s is None:
                continue
            xa, ya = s
            if len(xa) < 2 or x < xa[0] or x > xa[-1]:
                continue
            v = float(np.interp(x, xa, ya))
            readout[dd.name] = v
        s = self._series.get("__diff__")
        if s is not None:
            xa, ya = s
            if len(xa) >= 2 and xa[0] <= x <= xa[-1]:
                readout["Δz"] = float(np.interp(x, xa, ya))
        self.hover_moved.emit(x, readout)

    def _on_click(self, event):
        if event.inaxes != self.ax or event.button != 1:
            return
        if self.toolbar.mode:          # pan/zoom active — don't add markers
            return
        if event.xdata is None or event.ydata is None:
            return
        x, y = float(event.xdata), float(event.ydata)
        if self._mode == "ls":
            note = f"{format_chainage(x, self._chfmt)}  RL {y:.2f}"
        else:
            note = f"off {x:.1f}  RL {y:.2f}"
        self._markers_by_key.setdefault(self._active_key, []).append(
            (x, y, note))
        self.point_picked.emit(x, y)
        self.redraw()

    def _on_scroll(self, event):
        """Scroll-wheel zoom about the cursor (×/÷ 1.2 per notch). Both
        axes are scaled by the same factor, so the box aspect (and hence
        the vertical exaggeration) is preserved."""
        if event.inaxes != self.ax or event.xdata is None \
                or event.ydata is None:
            return
        scale = 1.0 / 1.2 if event.button == "up" else 1.2
        xd, yd = float(event.xdata), float(event.ydata)
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        self.ax.set_xlim(xd - (xd - x0) * scale, xd + (x1 - xd) * scale)
        self.ax.set_ylim(yd - (yd - y0) * scale, yd + (y1 - yd) * scale)
        self.canvas.draw_idle()

    # ------------------------------------------------------------------ #
    def copy_to_clipboard(self):
        """Copy the current plot to the system clipboard as an image."""
        from io import BytesIO
        from qgis.PyQt.QtGui import QImage
        buf = BytesIO()
        self.fig.savefig(buf, format="png", dpi=150,
                         facecolor=self.fig.get_facecolor())
        img = QImage.fromData(buf.getvalue(), "PNG")
        QApplication.clipboard().setImage(img)

    def render_figure(self, width_in=11.0, height_in=5.0, dpi=200):
        """A fresh standalone Figure of the current profile for export
        (independent of the on-screen canvas size)."""
        fig = Figure(figsize=(width_in, height_in), tight_layout=True)
        ax = fig.add_subplot(111)
        old_ax, old_fig = self.ax, self.fig
        try:
            self.ax, self.fig = ax, fig
            self.redraw()
        finally:
            exported = fig
            self.ax, self.fig = old_ax, old_fig
            self.redraw()
        return exported
