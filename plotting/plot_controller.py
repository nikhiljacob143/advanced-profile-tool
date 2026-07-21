# -*- coding: utf-8 -*-
"""Navigation and batch-render controller for the profile viewer.

Copyright (C) 2026 Nikhil Jacob — GPL v2 or later.
"""
import os

from ..export.image_exporter import build_image_name, save_figure


class PlotController:
    """Holds the current run's profiles and drives the plot widget."""

    def __init__(self, plot_widget):
        self.plot = plot_widget
        self.profiles = []          # list[ProfileResult] (cross-sections)
        self.long_profile = None
        self.dem_defs = []
        self.index = 0
        self.mode = "xs"

    # ------------------------------------------------------------------ #
    def load_run(self, profiles, long_profile, dem_defs):
        self.profiles = profiles or []
        self.long_profile = long_profile
        self.dem_defs = dem_defs or []
        self.index = 0
        self.refresh()

    def clear(self):
        self.profiles = []
        self.long_profile = None
        self.index = 0
        self.plot.show_profile(None, [])

    def refresh(self):
        if self.mode == "ls":
            self.plot.show_profile(self.long_profile, self.dem_defs, "ls")
        elif self.profiles:
            self.index = max(0, min(self.index, len(self.profiles) - 1))
            self.plot.show_profile(self.profiles[self.index],
                                   self.dem_defs, "xs")
        else:
            self.plot.show_profile(None, [])

    # ------------------------------------------------------------------ #
    def set_mode(self, mode):
        self.mode = "ls" if mode == "ls" else "xs"
        self.refresh()

    def go(self, index):
        self.index = index
        self.mode = "xs"
        self.refresh()

    def next(self):
        self.go(self.index + 1)

    def prev(self):
        self.go(self.index - 1)

    def current(self):
        if self.mode == "ls":
            return self.long_profile
        if self.profiles:
            return self.profiles[self.index]
        return None

    def labels(self):
        return [p.label for p in self.profiles]

    # ------------------------------------------------------------------ #
    def export_current_image(self, path, dpi=300):
        fig = self.plot.render_figure(dpi=dpi)
        return save_figure(fig, path, dpi=dpi)

    def export_all_images(self, folder, ext="png", dpi=200,
                          progress=None, is_cancelled=None):
        """Render every cross-section (and the long section) to `folder`.

        Returns list of written paths. progress: callable(0-100)."""
        written = []
        saved_index, saved_mode = self.index, self.mode
        n = len(self.profiles) + (1 if self.long_profile else 0)
        try:
            for i, p in enumerate(self.profiles):
                if is_cancelled and is_cancelled():
                    break
                self.index, self.mode = i, "xs"
                self.refresh()
                fig = self.plot.render_figure(dpi=dpi)
                path = os.path.join(folder,
                                    build_image_name(p.label, ext))
                written.append(save_figure(fig, path, dpi=dpi))
                if progress:
                    progress(100.0 * (i + 1) / max(n, 1))
            if self.long_profile and not (is_cancelled and is_cancelled()):
                self.mode = "ls"
                self.refresh()
                fig = self.plot.render_figure(width_in=16, height_in=5,
                                              dpi=dpi)
                path = os.path.join(
                    folder, build_image_name("long_section", ext))
                written.append(save_figure(fig, path, dpi=dpi))
                if progress:
                    progress(100.0)
        finally:
            self.index, self.mode = saved_index, saved_mode
            self.refresh()
        return written
