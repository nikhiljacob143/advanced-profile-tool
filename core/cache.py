# -*- coding: utf-8 -*-
"""Result cache for sampled profiles keyed on run parameters.

Keeps repeated viewing/exporting from resampling. Pure Python.
"""
import hashlib
import json


def _key(alignment, sections, dems, interval, extra=None):
    payload = {
        "align": (alignment.layer_id, alignment.feature_id,
                  alignment.part_index, alignment.reversed,
                  alignment.start_chainage,
                  round(alignment.length, 6)),
        "sections": [(s.section_id, round(s.chainage, 6),
                      round(s.left_width, 3), round(s.right_width, 3),
                      round(s.angle_offset_deg, 3)) for s in sections],
        "dems": [(d.layer_id, d.band, d.interp, round(d.v_offset, 4))
                 for d in dems if d.enabled],
        "interval": round(float(interval), 6),
        "extra": extra,
    }
    # SHA-1 is used purely as a content fingerprint for cache keys — it has
    # no security role here (hence usedforsecurity=False).
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True).encode(),
        usedforsecurity=False).hexdigest()


class ProfileCache:
    def __init__(self, max_entries=8):
        self._store = {}
        self._order = []
        self.max_entries = max_entries

    def get(self, alignment, sections, dems, interval, extra=None):
        return self._store.get(_key(alignment, sections, dems, interval,
                                    extra))

    def put(self, value, alignment, sections, dems, interval, extra=None):
        k = _key(alignment, sections, dems, interval, extra)
        if k not in self._store and len(self._order) >= self.max_entries:
            oldest = self._order.pop(0)
            self._store.pop(oldest, None)
        if k not in self._order:
            self._order.append(k)
        self._store[k] = value
        return k

    def clear(self):
        self._store.clear()
        self._order.clear()
