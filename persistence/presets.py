# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Named settings presets stored as JSON files.

Presets live in <QGIS settings dir>/advanced_profile_tool/presets by
default; a custom directory may be supplied (used by the tests, which
run without QGIS — QgsApplication is only imported lazily when no
directory is given).

All operations are tolerant: missing presets return None/False rather
than raising, and unreadable files are skipped.
"""
import json
import os
import re

_LAST_NAME = "__last__"
_SAFE_RE = re.compile(r"[^A-Za-z0-9 _\-\.]+")


def _sanitise(name):
    """Reduce a preset name to a safe filename stem."""
    stem = _SAFE_RE.sub("_", str(name)).strip().strip(".")
    return stem or "preset"


class PresetStore:
    """Load/save named settings presets as JSON files."""

    def __init__(self, dir_path=None):
        if dir_path is None:
            # Lazy import so headless tests can construct the store with
            # an explicit directory without QGIS installed.
            from qgis.core import QgsApplication
            dir_path = os.path.join(QgsApplication.qgisSettingsDirPath(),
                                    "advanced_profile_tool", "presets")
        self.dir_path = dir_path

    # ------------------------------------------------------------------ #
    def _path(self, name):
        return os.path.join(self.dir_path, _sanitise(name) + ".json")

    def _ensure_dir(self):
        try:
            os.makedirs(self.dir_path, exist_ok=True)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------ #
    def list_presets(self):
        """Names of stored presets (the auto-preset is excluded)."""
        try:
            files = os.listdir(self.dir_path)
        except OSError:
            return []
        names = []
        for f in sorted(files):
            stem, ext = os.path.splitext(f)
            if ext.lower() == ".json" and stem != _LAST_NAME:
                names.append(stem)
        return names

    def save(self, name, settings_dict):
        """Write a preset. Returns True on success."""
        if not self._ensure_dir():
            return False
        payload = {"name": str(name), "settings": dict(settings_dict or {}),
                   "version": 1}
        try:
            with open(self._path(name), "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def load(self, name):
        """Read a preset's settings dict, or None when missing/invalid."""
        try:
            with open(self._path(name), "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            return None
        if isinstance(payload, dict) and "settings" in payload:
            settings = payload.get("settings")
            return settings if isinstance(settings, dict) else None
        # tolerate bare settings dicts written by hand
        return payload if isinstance(payload, dict) else None

    def delete(self, name):
        """Remove a preset. Returns True when a file was deleted."""
        try:
            os.remove(self._path(name))
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------ #
    def export_file(self, name, path):
        """Copy a preset to an external JSON file. Returns True on
        success, False when the preset does not exist."""
        settings = self.load(name)
        if settings is None:
            return False
        payload = {"name": str(name), "settings": settings, "version": 1}
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def import_file(self, path):
        """Import a preset from an external JSON file.

        Returns the stored preset name, or None when the file is
        missing/unreadable. The embedded name is used when present,
        otherwise the file stem.
        """
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            return None
        if isinstance(payload, dict) and "settings" in payload:
            name = payload.get("name") or \
                os.path.splitext(os.path.basename(path))[0]
            settings = payload.get("settings")
        elif isinstance(payload, dict):
            name = os.path.splitext(os.path.basename(path))[0]
            settings = payload
        else:
            return None
        if not isinstance(settings, dict):
            return None
        name = _sanitise(name)
        if name == _LAST_NAME:
            name = "imported"
        return name if self.save(name, settings) else None

    # ---- auto-preset (last used settings) ----------------------------- #
    def save_last(self, settings_dict):
        """Persist the most recent run settings under the reserved name."""
        return self.save(_LAST_NAME, settings_dict)

    def load_last(self):
        """Settings from the most recent run, or None."""
        return self.load(_LAST_NAME)
