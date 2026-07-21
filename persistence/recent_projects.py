# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Most-recently-used path list, storage-agnostic.

No module-level qgis import: pass a getter/setter pair for testing, or
let the class fall back to QgsSettings via a lazy import inside the
default accessors.
"""

import logging

from ..constants import SETTINGS_GROUP

_LOG = logging.getLogger(__name__)

DEFAULT_KEY = f"{SETTINGS_GROUP}/recent_paths"


def _default_getter(key):
    from qgis.core import QgsSettings  # lazy — only in a QGIS session
    return QgsSettings().value(key, [])


def _default_setter(key, value):
    from qgis.core import QgsSettings  # lazy — only in a QGIS session
    QgsSettings().setValue(key, value)


class RecentList:
    """A small most-recently-used list of path strings.

    Parameters
    ----------
    key : str
        Storage key.
    maxlen : int
        Maximum number of entries retained.
    getter, setter : callables
        getter(key) -> stored value; setter(key, value). When omitted,
        QgsSettings is used (lazy import).
    """

    def __init__(self, key=DEFAULT_KEY, maxlen=8, getter=None, setter=None):
        self.key = key
        self.maxlen = int(maxlen)
        self._get = getter or _default_getter
        self._set = setter or _default_setter

    def items(self):
        """Stored paths, most recent first."""
        try:
            raw = self._get(self.key)
        except Exception:
            return []
        if raw is None:
            return []
        if isinstance(raw, str):
            raw = [raw] if raw else []
        return [str(v) for v in raw][:self.maxlen]

    def add(self, pathstr):
        """Insert (or promote) a path at the front. Returns the new list."""
        path = str(pathstr)
        items = [p for p in self.items() if p != path]
        items.insert(0, path)
        items = items[:self.maxlen]
        try:
            self._set(self.key, items)
        except Exception:                              # noqa: BLE001
            _LOG.debug("Persisting recent list failed; kept in memory only",
                       exc_info=True)
        return items
