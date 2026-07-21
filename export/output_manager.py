# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Output folder management for export runs.

Creates a timestamped run directory beneath a user-selected base folder,
organises outputs into per-format subfolders and guarantees that existing
files are never overwritten (a numeric suffix is appended instead).

Pure Python — no qgis imports, so the module is testable headless.
"""
import datetime
import json
import logging
import os

logger = logging.getLogger(__name__)

#: Recognised output kinds → subfolder names.
OUTPUT_KINDS = ("csv", "excel", "pdf", "images", "dxf", "gis")

MANIFEST_NAME = "run_parameters.json"


class OutputManager:
    """Manage the folder structure for one export run.

    Parameters
    ----------
    base_dir : str
        User-selected output base folder.
    timestamped : bool
        When True (default) a ``run_YYYYmmdd_HHMMSS`` subfolder is created
        beneath ``base_dir``; when False outputs go directly into
        ``base_dir``.
    subfolders : bool
        When True (default) files are organised into per-kind subfolders
        (csv, excel, pdf, images, dxf, gis); when False all files are
        placed flat in the run directory.
    """

    def __init__(self, base_dir, timestamped=True, subfolders=True):
        self.base_dir = str(base_dir)
        self.timestamped = bool(timestamped)
        self.subfolders = bool(subfolders)
        self._run_dir = None

    def run_dir(self):
        """Return (creating once per instance) the run directory path.

        The directory name is fixed on the first call and cached, so all
        outputs of one run share the same timestamp.
        """
        if self._run_dir is None:
            if self.timestamped:
                stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                path = os.path.join(self.base_dir, f"run_{stamp}")
                # never merge into an existing folder from a previous run
                path = self._unique_dir(path)
            else:
                path = self.base_dir
            os.makedirs(path, exist_ok=True)
            self._run_dir = path
            logger.info("Output run directory: %s", path)
        return self._run_dir

    def path_for(self, kind, filename):
        """Return the full path for ``filename`` of the given output kind.

        ``kind`` must be one of :data:`OUTPUT_KINDS`. The kind subfolder is
        created on demand; when ``subfolders`` is False files are placed
        flat in the run directory. The returned path is made unique so an
        existing file is never overwritten.
        """
        if kind not in OUTPUT_KINDS:
            raise ValueError(
                f"Unknown output kind '{kind}'. "
                f"Expected one of: {', '.join(OUTPUT_KINDS)}.")
        if self.subfolders:
            folder = os.path.join(self.run_dir(), kind)
        else:
            folder = self.run_dir()
        os.makedirs(folder, exist_ok=True)
        return self.unique_path(os.path.join(folder, filename))

    @staticmethod
    def unique_path(path):
        """Return ``path`` unchanged when free, else append ``_1``, ``_2``…

        The suffix is inserted before the file extension. This protects
        against overwriting files outside the managed run directory.
        """
        if not os.path.exists(path):
            return path
        root, ext = os.path.splitext(path)
        n = 1
        while True:
            candidate = f"{root}_{n}{ext}"
            if not os.path.exists(candidate):
                return candidate
            n += 1

    @staticmethod
    def _unique_dir(path):
        """Return a directory path that does not yet exist (``_1``, ``_2``…)."""
        if not os.path.exists(path):
            return path
        n = 1
        while os.path.exists(f"{path}_{n}"):
            n += 1
        return f"{path}_{n}"

    def manifest(self, params):
        """Write ``params`` (a dict) as ``run_parameters.json`` in the run
        directory and return the file path.

        Non-serialisable values are converted with ``str()`` so dataclasses
        or numpy scalars do not abort the export.
        """
        path = os.path.join(self.run_dir(), MANIFEST_NAME)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(params, fh, indent=2, default=str, ensure_ascii=False)
        return path
