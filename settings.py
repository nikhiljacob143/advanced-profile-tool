# -*- coding: utf-8 -*-
"""Settings access: factory defaults ← user settings (QgsSettings) ←
project-stored values (QgsProject custom properties)."""
import json

from qgis.core import QgsProject, QgsSettings

from .constants import DEFAULTS, SETTINGS_GROUP, PROJECT_SCOPE


class SettingsManager:
    """Read/write plugin settings with precedence:
    project > user > factory default."""

    def get(self, key, default=None):
        fallback = DEFAULTS.get(key, default)
        s = QgsSettings()
        user_val = s.value(f"{SETTINGS_GROUP}/{key}", fallback)
        proj_val, ok = QgsProject.instance().readEntry(
            PROJECT_SCOPE, key, "")
        if ok and proj_val != "":
            try:
                return json.loads(proj_val)
            except (ValueError, TypeError):
                return proj_val
        return _coerce(user_val, fallback)

    def set_user(self, key, value):
        QgsSettings().setValue(f"{SETTINGS_GROUP}/{key}", value)

    def set_project(self, key, value):
        QgsProject.instance().writeEntry(
            PROJECT_SCOPE, key, json.dumps(value))

    def clear_project(self, key):
        QgsProject.instance().removeEntry(PROJECT_SCOPE, key)

    def all_settings(self):
        return {k: self.get(k) for k in DEFAULTS}

    def save_all_project(self, mapping):
        for k, v in mapping.items():
            self.set_project(k, v)


def _coerce(value, template):
    """QgsSettings returns strings for numbers/bools — coerce to the
    template's type."""
    if template is None or value is None:
        return value
    t = type(template)
    if t is bool:
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "yes")
    if t in (int, float):
        try:
            return t(value)
        except (TypeError, ValueError):
            return template
    return value
