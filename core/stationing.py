# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Station equations: piecewise re-numbering of displayed chainage.

A station equation declares that, from a given raw chainage onward, the
displayed chainage restarts at an "ahead" value. This supports realigned
routes where the field stationing carries equations such as
"CH 1+200 back = CH 1+500 ahead".

Definitions
-----------
* raw chainage — geometric distance along the alignment plus the
  alignment start chainage (i.e. the displayed chainage BEFORE any
  equation is applied).
* equation (raw_from, ahead_value) — at raw chainage ``raw_from`` the
  displayed chainage becomes ``ahead_value`` and increases at 1:1 with
  raw chainage until the next equation.

Piecewise mapping (equations sorted by raw_from):
    displayed = ahead_value_k + (raw - raw_from_k)
        for the greatest raw_from_k <= raw
    displayed = raw + start_offset      before the first equation

Overlapping displayed ranges (a "station back" equation) and
non-monotonic ahead values are permitted but reported by
:meth:`StationEquations.validate` — the inverse mapping is then
ambiguous and resolves to the FIRST matching region.

Pure Python — no qgis imports; fully unit-testable.
"""
import logging

_LOG = logging.getLogger(__name__)


__all__ = ["StationEquations"]


class StationEquations:
    """Piecewise chainage re-numbering from a list of station equations.

    Parameters
    ----------
    pairs : iterable of (raw_from, ahead_value) or [raw_from, ahead_value]
        Station equations; the settings value (list of two-element lists)
        is accepted directly. Non-numeric entries are ignored. Pairs are
        stored sorted by raw_from.
    start_offset : float
        Added to raw chainage BEFORE the first equation (0.0 when the
        alignment start chainage is already included in the raw value).
    """

    def __init__(self, pairs=None, start_offset=0.0):
        self.start_offset = float(start_offset)
        cleaned = []
        for p in (pairs or []):
            try:
                raw_from = float(p[0])
                ahead = float(p[1])
            except (TypeError, ValueError, IndexError):
                _LOG.debug("Skipping malformed station equation pair %r", p)
                continue
            cleaned.append((raw_from, ahead))
        self.pairs = sorted(cleaned, key=lambda t: t[0])

    # ------------------------------------------------------------------ #
    def __bool__(self):
        return bool(self.pairs)

    def __len__(self):
        return len(self.pairs)

    def __iter__(self):
        return iter(self.pairs)

    # ------------------------------------------------------------------ #
    def apply(self, raw_chainage):
        """Raw chainage → displayed chainage (piecewise mapping)."""
        raw = float(raw_chainage)
        result = raw + self.start_offset
        for raw_from, ahead in self.pairs:
            if raw_from <= raw:
                result = ahead + (raw - raw_from)
            else:
                break
        return result

    def inverse(self, displayed):
        """Displayed chainage → raw chainage (first matching region).

        Regions are examined in raw-chainage order (the base region
        before the first equation, then each equation region). When
        displayed ranges overlap (a station-back equation), the FIRST
        region containing ``displayed`` wins. Returns None when no
        region maps to the requested displayed chainage (a displayed
        value that falls inside a stationing gap).
        """
        d = float(displayed)
        if not self.pairs:
            return d - self.start_offset
        # base region: raw < raw_from_0
        first_raw = self.pairs[0][0]
        raw = d - self.start_offset
        if raw < first_raw:
            return raw
        for k, (raw_from, ahead) in enumerate(self.pairs):
            raw = raw_from + (d - ahead)
            if d < ahead or raw < raw_from:
                continue
            if k + 1 < len(self.pairs):
                next_from = self.pairs[k + 1][0]
                if raw >= next_from:
                    continue
            return raw
        return None

    # ------------------------------------------------------------------ #
    @staticmethod
    def validate(pairs):
        """Check a raw settings list of station equations.

        Overlapping displayed ranges and non-monotonic ahead values are
        permitted (the mapping stays well defined forward) but each is
        reported so the user can confirm the intent. Returns a list of
        warning strings (empty when clean).
        """
        warnings = []
        cleaned = []
        for i, p in enumerate(pairs or []):
            try:
                raw_from = float(p[0])
                ahead = float(p[1])
            except (TypeError, ValueError, IndexError):
                warnings.append(
                    f"Station equation {i + 1} is not a numeric "
                    "[raw, ahead] pair and was ignored.")
                continue
            cleaned.append((raw_from, ahead))
        ordered = sorted(cleaned, key=lambda t: t[0])
        for (rf0, ah0), (rf1, ah1) in zip(ordered, ordered[1:]):
            if abs(rf1 - rf0) < 1e-9:
                warnings.append(
                    f"Two station equations share the same raw chainage "
                    f"{rf0:g}; only the later ahead value takes effect.")
                continue
            end_disp = ah0 + (rf1 - rf0)   # displayed at end of region k
            if ah1 < end_disp - 1e-9:
                warnings.append(
                    f"Equation at raw {rf1:g} (ahead {ah1:g}) overlaps the "
                    f"previous displayed range (which reaches {end_disp:g}): "
                    "displayed chainages will repeat.")
            if ah1 <= ah0 + 1e-9:
                warnings.append(
                    f"Ahead values are non-monotonic: raw {rf1:g} restarts "
                    f"at {ah1:g}, not greater than the previous ahead value "
                    f"{ah0:g}.")
        return warnings
