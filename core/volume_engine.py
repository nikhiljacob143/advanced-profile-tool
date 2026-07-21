# -*- coding: utf-8 -*-
"""Average-end-area volumes with optional prismoidal correction.

Pure Python/numpy — no qgis imports.
"""
from .data_models import VolumeRow


def average_end_area(comparisons, prismoidal=False, max_bridged=1):
    """Volumes between consecutive VALID sections.

    comparisons: list[SectionComparison] ordered by chainage.
    prismoidal:  apply the prismoidal (Simpson-like) correction
                 V = L/6 * (A1 + 4*Am + A2) using the mean area for Am when
                 only end areas are known → reduces to the classic
                 correction V = L/2*(A1+A2) - L/6*(A1 - 2*sqrt(A1*A2) + A2).
    max_bridged: maximum number of consecutive INVALID sections that may be
                 skipped when pairing (more → the pair is not formed and a
                 warning entry is produced by the caller via `skipped`).

    Returns (rows:list[VolumeRow], totals:dict, skipped:list[str]).
    """
    valid = [c for c in comparisons if c.valid]
    rows = []
    skipped = []
    # report invalid runs longer than max_bridged
    run = []
    for c in comparisons:
        if not c.valid:
            run.append(c)
        else:
            if len(run) > max_bridged:
                skipped.append(
                    f"{len(run)} consecutive invalid sections "
                    f"({run[0].label} to {run[-1].label}) — volumes not "
                    "bridged across this span.")
            run = []
    if len(run) > max_bridged:
        skipped.append(
            f"{len(run)} trailing invalid sections ignored.")

    cum_cut = cum_fill = 0.0
    for a, b in zip(valid, valid[1:]):
        L = b.chainage - a.chainage
        if L <= 0:
            skipped.append(
                f"Non-increasing chainage pair {a.label} → {b.label}; "
                "pair skipped.")
            continue
        # do not bridge across a gap of more than max_bridged invalid sections
        between = [c for c in comparisons
                   if a.chainage < c.chainage < b.chainage]
        n_invalid = sum(1 for c in between if not c.valid)
        if n_invalid > max_bridged:
            skipped.append(
                f"Span {a.label} → {b.label} contains {n_invalid} invalid "
                "sections; volume not computed for this span.")
            continue
        if prismoidal:
            cut_v = _prismoidal(a.cut_area, b.cut_area, L)
            fill_v = _prismoidal(a.fill_area, b.fill_area, L)
        else:
            cut_v = 0.5 * (a.cut_area + b.cut_area) * L
            fill_v = 0.5 * (a.fill_area + b.fill_area) * L
        cum_cut += cut_v
        cum_fill += fill_v
        rows.append(VolumeRow(
            from_id=a.section_id, to_id=b.section_id,
            from_chainage=a.chainage, to_chainage=b.chainage,
            length=L, cut_volume=cut_v, fill_volume=fill_v,
            cum_cut=cum_cut, cum_fill=cum_fill))
    totals = {"cut": cum_cut, "fill": cum_fill,
              "net": cum_fill - cum_cut,
              "spans": len(rows)}
    return rows, totals, skipped


def _prismoidal(a1, a2, L):
    """Prismoidal formula with the middle area estimated as the area of the
    mean section: Am ≈ ((sqrt(A1)+sqrt(A2))/2)^2, standard earthworks
    practice when the middle section is not separately measured."""
    import math
    am = ((math.sqrt(max(a1, 0.0)) + math.sqrt(max(a2, 0.0))) / 2.0) ** 2
    return L / 6.0 * (a1 + 4.0 * am + a2)


def mass_haul(rows):
    """Cumulative net volume ordinates (chainage, cum_net) for plotting."""
    pts = []
    cum = 0.0
    for r in rows:
        cum += r.net_volume
        pts.append((r.to_chainage, cum))
    return pts
