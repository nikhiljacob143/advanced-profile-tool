# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""Title block data for PDF section sheets.

Pure Python — no qgis imports, no reportlab imports; the PDF composer
consumes the plain dict produced here.
"""
import datetime
from dataclasses import asdict, dataclass, field

__all__ = ["TitleBlock", "default_title_block"]


@dataclass
class TitleBlock:
    """Drawing title block fields for section sheet PDFs.

    All fields are optional strings; empty fields render as blank boxes.
    """
    project: str = ""
    client: str = ""
    site: str = ""
    alignment: str = ""
    date: str = ""
    author: str = ""
    reviewer: str = ""
    drawing_number: str = ""
    revision: str = ""

    def as_dict(self):
        """Return the title block as a plain dict of strings."""
        return {k: ("" if v is None else str(v))
                for k, v in asdict(self).items()}


def _setting(settings, key, default=""):
    """Read a string setting; tolerate dicts, managers or None."""
    if settings is None:
        return default
    try:
        value = settings.get(key, default)
    except (TypeError, AttributeError):
        return default
    return default if value is None else str(value)


def default_title_block(settings, alignment_name):
    """Build a :class:`TitleBlock` from the plugin settings.

    Reads the ``tb_project``, ``tb_client``, ``tb_site``, ``tb_author``,
    ``tb_reviewer``, ``tb_drawing_number`` and ``tb_revision`` settings
    (all default empty); the alignment name is supplied by the caller and
    the date is filled automatically with today's ISO date.
    """
    return TitleBlock(
        project=_setting(settings, "tb_project"),
        client=_setting(settings, "tb_client"),
        site=_setting(settings, "tb_site"),
        alignment="" if alignment_name is None else str(alignment_name),
        date=datetime.date.today().isoformat(),
        author=_setting(settings, "tb_author"),
        reviewer=_setting(settings, "tb_reviewer"),
        drawing_number=_setting(settings, "tb_drawing_number"),
        revision=_setting(settings, "tb_revision"),
    )
