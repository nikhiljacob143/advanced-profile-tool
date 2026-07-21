# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""CSV exporters for long sections, cross-sections, comparisons and volumes.

All writers use the ``csv`` module with a configurable delimiter
(settings key ``csv_delimiter``, default ``,``) and the plugin decimal
settings. NaN elevations (NoData) are written as empty cells. Column
headers carry SI units in parentheses: lengths/coordinates (m), areas
(m²), volumes (m³); elevation columns are named "<dem> RL (m)".

Every export function accepts an optional ``metadata`` dict; when given,
two comment lines are written before the header::

    # Advanced Profile Tool <version> — <table name> — generated <ISO dt>
    # alignment: <name> | sampling interval <v> m | convention: fill =
      comparison above reference

Recognised metadata keys: ``alignment`` (str) and ``sampling_interval``
(float). When ``metadata`` is None the comment lines are skipped.

Offsets follow the plugin convention: negative = left of the alignment,
positive = right. Pure Python — no qgis imports.
"""
import csv
import datetime
import math

from ..constants import DEFAULTS, PLUGIN_NAME, PLUGIN_VERSION

__all__ = ["export_long_section", "export_cross_sections",
           "export_comparison", "export_volumes"]


def _setting(settings, key, fallback=None):
    """Read ``key`` from a settings mapping/manager with factory fallback."""
    default = DEFAULTS.get(key, fallback)
    if settings is None:
        return default
    try:
        value = settings.get(key, default)
    except (TypeError, AttributeError):
        return default
    return default if value is None else value


def _decimals(settings):
    """Return (chainage, elevation, offset) decimal places from settings."""
    return (int(_setting(settings, "decimals_chainage", 2)),
            int(_setting(settings, "decimals_elevation", 3)),
            int(_setting(settings, "decimals_offset", 2)))


def _num(value, decimals):
    """Format a number to fixed decimals; NaN/None → empty string."""
    if value is None:
        return ""
    v = float(value)
    if math.isnan(v):
        return ""
    return f"{v:.{decimals}f}"


def _enabled(dem_defs):
    """Return the enabled DEM definitions in given order."""
    return [d for d in dem_defs if getattr(d, "enabled", True)]


def _open_writer(path, settings):
    """Open ``path`` for CSV writing and return (file, writer)."""
    delimiter = str(_setting(settings, "csv_delimiter", ","))
    fh = open(path, "w", newline="", encoding="utf-8-sig")
    return fh, csv.writer(fh, delimiter=delimiter)


def _write_metadata(fh, table_name, metadata):
    """Write the two leading comment lines when ``metadata`` is given.

    ``metadata`` keys used: ``alignment`` (name) and ``sampling_interval``
    (metres). None → no comment lines (graceful skip).
    """
    if metadata is None:
        return
    try:
        alignment = str(metadata.get("alignment", "") or "")
        interval = metadata.get("sampling_interval", "")
    except AttributeError:
        return
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    fh.write(f"# {PLUGIN_NAME} {PLUGIN_VERSION} — {table_name} — "
             f"generated {stamp}\n")
    fh.write(f"# alignment: {alignment} | sampling interval {interval} m "
             f"| convention: fill = comparison above reference\n")


def _dem_headers(dems):
    """Elevation column titles: '<dem name> RL (m)'."""
    return [f"{d.name} RL (m)" for d in dems]


def _elevation_row_values(result, index, dems, dec_elev):
    """Per-DEM elevation cells for sample ``index`` of ``result``."""
    cells = []
    for dem in dems:
        line = result.lines.get(dem.layer_id)
        if line is None or line.elevations is None:
            cells.append("")
        else:
            cells.append(_num(line.elevations[index], dec_elev))
    return cells


def export_long_section(path, profile_result, dem_defs, settings,
                        metadata=None):
    """Write the long section to CSV.

    Columns: chainage (m), x (m), y (m), then one "<dem> RL (m)" column
    per enabled DEM. For a long section ``profile_result.offsets`` holds
    chainages. ``metadata`` (optional dict) adds two leading comment
    lines. Returns the path written.
    """
    dec_ch, dec_elev, dec_off = _decimals(settings)
    dems = _enabled(dem_defs)
    fh, writer = _open_writer(path, settings)
    try:
        _write_metadata(fh, "Long section", metadata)
        writer.writerow(["chainage (m)", "x (m)", "y (m)"]
                        + _dem_headers(dems))
        n = len(profile_result.offsets) if profile_result.offsets is not None else 0
        for i in range(n):
            row = [_num(profile_result.offsets[i], dec_ch),
                   _num(profile_result.xs[i], dec_off),
                   _num(profile_result.ys[i], dec_off)]
            row += _elevation_row_values(profile_result, i, dems, dec_elev)
            writer.writerow(row)
    finally:
        fh.close()
    return path


def export_cross_sections(path, profile_results, dem_defs, settings,
                          metadata=None):
    """Write all cross-sections to one consolidated CSV.

    Columns: section_label, chainage (m), offset (m), x (m), y (m), then
    one "<dem> RL (m)" column per enabled DEM. Offsets are signed
    (negative = left). ``metadata`` (optional dict) adds two leading
    comment lines. Returns the path written.
    """
    dec_ch, dec_elev, dec_off = _decimals(settings)
    dems = _enabled(dem_defs)
    fh, writer = _open_writer(path, settings)
    try:
        _write_metadata(fh, "Cross sections", metadata)
        writer.writerow(["section_label", "chainage (m)", "offset (m)",
                         "x (m)", "y (m)"] + _dem_headers(dems))
        for result in profile_results:
            n = len(result.offsets) if result.offsets is not None else 0
            ch = _num(result.chainage, dec_ch)
            for i in range(n):
                row = [result.label, ch,
                       _num(result.offsets[i], dec_off),
                       _num(result.xs[i], dec_off),
                       _num(result.ys[i], dec_off)]
                row += _elevation_row_values(result, i, dems, dec_elev)
                writer.writerow(row)
    finally:
        fh.close()
    return path


def export_comparison(path, comparisons, settings, metadata=None):
    """Write per-section cut/fill areas to CSV.

    Columns: label, chainage (m), cut_area (m²), fill_area (m²),
    net_area (m²), gap_length (m), valid. Areas use the elevation
    decimal setting. ``metadata`` (optional dict) adds two leading
    comment lines. Returns the path.
    """
    dec_ch, dec_elev, dec_off = _decimals(settings)
    fh, writer = _open_writer(path, settings)
    try:
        _write_metadata(fh, "Comparison", metadata)
        writer.writerow(["label", "chainage (m)", "cut_area (m²)",
                         "fill_area (m²)", "net_area (m²)",
                         "gap_length (m)", "valid"])
        for c in comparisons:
            writer.writerow([
                c.label,
                _num(c.chainage, dec_ch),
                _num(c.cut_area, dec_elev),
                _num(c.fill_area, dec_elev),
                _num(c.net_area, dec_elev),
                _num(c.gap_length, dec_off),
                "yes" if c.valid else "no",
            ])
    finally:
        fh.close()
    return path


def export_volumes(path, volume_rows, totals, settings, metadata=None):
    """Write average-end-area volumes to CSV with a totals row at the end.

    Columns: from_label, to_label, from_chainage (m), to_chainage (m),
    length (m), cut_volume (m³), fill_volume (m³), net_volume (m³),
    cum_cut (m³), cum_fill (m³), cum_net (m³). ``totals`` is the dict
    returned by the volume engine (keys ``cut``, ``fill``, ``net``).
    ``metadata`` (optional dict) adds two leading comment lines.
    Returns the path written.
    """
    dec_ch, dec_elev, dec_off = _decimals(settings)
    fh, writer = _open_writer(path, settings)
    try:
        _write_metadata(fh, "Volumes", metadata)
        writer.writerow(["from_label", "to_label", "from_chainage (m)",
                         "to_chainage (m)", "length (m)",
                         "cut_volume (m³)", "fill_volume (m³)",
                         "net_volume (m³)", "cum_cut (m³)",
                         "cum_fill (m³)", "cum_net (m³)"])
        for r in volume_rows:
            writer.writerow([
                getattr(r, "from_label", "") or f"S{r.from_id}",
                getattr(r, "to_label", "") or f"S{r.to_id}",
                _num(r.from_chainage, dec_ch),
                _num(r.to_chainage, dec_ch),
                _num(r.length, dec_ch),
                _num(r.cut_volume, dec_elev),
                _num(r.fill_volume, dec_elev),
                _num(r.net_volume, dec_elev),
                _num(r.cum_cut, dec_elev),
                _num(r.cum_fill, dec_elev),
                _num(r.cum_net, dec_elev),
            ])
        totals = totals or {}
        writer.writerow([
            "TOTAL", "", "", "", "",
            _num(totals.get("cut", 0.0), dec_elev),
            _num(totals.get("fill", 0.0), dec_elev),
            _num(totals.get("net", 0.0), dec_elev),
            "", "", "",
        ])
    finally:
        fh.close()
    return path
