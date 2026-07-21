# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Persist the run setup (settings, DEM definitions, alignment metadata)
in the QGIS project file so a saved project reopens with the last
configuration.

Stored as a single JSON string under the project custom-property scope
"AdvancedProfileTool", key "run_setup". DemDef objects are converted to
plain dicts with dataclasses.asdict; load returns dicts (callers may
rebuild DemDef via dem_defs_from_dicts).
"""
import json
from dataclasses import asdict, is_dataclass

from qgis.core import Qgis, QgsMessageLog, QgsProject

from ..constants import LOG_TAG, PROJECT_SCOPE
from ..core.data_models import DemDef

RUN_SETUP_KEY = "run_setup"


def save_run_setup(settings_dict, dem_defs, alignment_meta):
    """Write the run setup to the current project.

    Parameters
    ----------
    settings_dict : dict
    dem_defs : list[DemDef] or list[dict]
    alignment_meta : dict (layer id, feature ids, direction, chainage
        offsets — whatever the caller needs to restore its selection)

    Returns True on success.
    """
    dems = []
    for d in dem_defs or []:
        if is_dataclass(d):
            dems.append(asdict(d))
        elif isinstance(d, dict):
            dems.append(dict(d))
    payload = {
        "settings": dict(settings_dict or {}),
        "dems": dems,
        "alignment": dict(alignment_meta or {}),
        "version": 1,
    }
    try:
        text = json.dumps(payload)
    except (TypeError, ValueError) as exc:
        QgsMessageLog.logMessage(
            f"Run setup not saved (not JSON-serialisable): {exc}",
            LOG_TAG, Qgis.MessageLevel.Warning)
        return False
    return bool(QgsProject.instance().writeEntry(
        PROJECT_SCOPE, RUN_SETUP_KEY, text))


def load_run_setup():
    """Read the run setup from the current project.

    Returns (settings_dict, dem_defs_as_dicts, alignment_meta) or
    (None, None, None) when nothing usable is stored.
    """
    text, ok = QgsProject.instance().readEntry(
        PROJECT_SCOPE, RUN_SETUP_KEY, "")
    if not ok or not text:
        return None, None, None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        QgsMessageLog.logMessage(
            "Stored run setup could not be parsed; ignored.",
            LOG_TAG, Qgis.MessageLevel.Warning)
        return None, None, None
    if not isinstance(payload, dict):
        return None, None, None
    return (payload.get("settings"), payload.get("dems"),
            payload.get("alignment"))


def clear_run_setup():
    """Remove any stored run setup from the current project."""
    QgsProject.instance().removeEntry(PROJECT_SCOPE, RUN_SETUP_KEY)


def dem_defs_from_dicts(dem_dicts):
    """Rebuild DemDef objects from stored dicts (unknown keys dropped)."""
    out = []
    valid = {f for f in DemDef.__dataclass_fields__}
    for d in dem_dicts or []:
        if not isinstance(d, dict):
            continue
        out.append(DemDef(**{k: v for k, v in d.items() if k in valid}))
    return out
