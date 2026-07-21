# -*- coding: utf-8 -*-
# Copyright (C) 2026 Nikhil Jacob — GPL v2 or later
"""PDF section sheet composer built on reportlab.

Each sheet carries one (or a 2x2 grid of four) pre-rendered section plot
images with a page border, a labelled title block strip along the bottom,
an optional logo and "Sheet N of M" numbering. Grid and axis content is
part of the PNG images rendered by the profile viewer — this module only
composes pages, it does not replot.

Two-pass operation: :meth:`SectionSheetPdf.add_profile_image` collects
entries; :meth:`SectionSheetPdf.save` lays out all pages once the total
sheet count is known. No qgis imports.
"""
import logging
import math
import os

try:
    from reportlab.lib import pagesizes
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as pdf_canvas
    HAVE_REPORTLAB = True
except ImportError:                                   # pragma: no cover
    HAVE_REPORTLAB = False

logger = logging.getLogger(__name__)

__all__ = ["SectionSheetPdf", "MplFallbackPdf", "make_pdf_composer",
           "HAVE_REPORTLAB"]


class MplFallbackPdf:
    """Minimal PDF composer used when reportlab is unavailable.

    Same public interface subset as :class:`SectionSheetPdf`
    (``add_profile_image`` + ``save``): one pre-rendered plot image per
    page with a caption line carrying the label, chainage and notes.
    Built on matplotlib's PdfPages, which ships with QGIS everywhere.
    """

    _SIZES_IN = {"A4": (11.69, 8.27), "A3": (16.54, 11.69),
                 "A1": (33.11, 23.39)}       # landscape (w, h) inches

    def __init__(self, path, page="A3", landscape=True, title_block=None,
                 logo_path=None, sections_per_page=1):
        self.path = path
        self._size = self._SIZES_IN.get(page, self._SIZES_IN["A3"])
        self._tb = dict(title_block or {})
        self._entries = []

    def add_profile_image(self, png_path, section_label, chainage_text,
                          extra_notes=None, scale_text=None):
        # scale_text mirrors SectionSheetPdf's signature; the matplotlib
        # fallback does not draw a scale box (ignored).
        _ = scale_text
        self._entries.append((png_path, section_label, chainage_text,
                              extra_notes or ""))

    def save(self):
        import matplotlib
        matplotlib.use("Agg", force=False)
        import matplotlib.image as mpimg
        from matplotlib.backends.backend_pdf import PdfPages
        from matplotlib.figure import Figure
        tb_line = "  |  ".join(
            f"{k}: {v}" for k, v in self._tb.items() if v)
        with PdfPages(self.path) as pdf:
            n = len(self._entries)
            for i, (png, label, chain, notes) in enumerate(self._entries):
                fig = Figure(figsize=self._size)
                ax = fig.add_axes([0.03, 0.10, 0.94, 0.84])
                ax.axis("off")
                try:
                    ax.imshow(mpimg.imread(png))
                except (OSError, ValueError):
                    ax.text(0.5, 0.5, f"Image missing:\n{png}",
                            ha="center", va="center")
                fig.suptitle(f"{label}   {chain}   {notes}", fontsize=11)
                fig.text(0.03, 0.03, tb_line, fontsize=7)
                fig.text(0.97, 0.03, f"Sheet {i + 1} of {n}",
                         fontsize=8, ha="right")
                pdf.savefig(fig)
        return self.path


def make_pdf_composer(path, **kwargs):
    """SectionSheetPdf when reportlab is present, else the matplotlib
    fallback — callers never need to branch."""
    cls = SectionSheetPdf if HAVE_REPORTLAB else MplFallbackPdf
    return cls(path, **kwargs)

_PAGE_SIZES = {"A4": pagesizes.A4, "A3": pagesizes.A3, "A1": pagesizes.A1}

_MARGIN = 10 * mm            # page border inset
_TB_HEIGHT = 22 * mm         # title block strip height
_LOGO_WIDTH = 40 * mm        # logo box width (when a logo is supplied)

# title block cells: (row, attribute, printed label); row 0 is the upper row
_TB_FIELDS = (
    (0, "project", "PROJECT"),
    (0, "client", "CLIENT"),
    (0, "site", "SITE"),
    (0, "alignment", "ALIGNMENT"),
    (0, "date", "DATE"),
    (1, "author", "DRAWN"),
    (1, "reviewer", "REVIEWED"),
    (1, "drawing_number", "DRAWING NO."),
    (1, "revision", "REV"),
    (1, "__sheet__", "SHEET"),
)


class SectionSheetPdf:
    """Compose section plot images into a bordered, title-blocked PDF.

    Parameters
    ----------
    path : str
        Output PDF path.
    page : str
        Page size name: "A4", "A3" (default) or "A1".
    landscape : bool
        Landscape orientation when True (default).
    title_block : TitleBlock | dict | None
        Title block fields (project, client, site, alignment, date,
        author, reviewer, drawing_number, revision) — all optional.
    logo_path : str | None
        Optional raster logo placed at the left of the title block,
        scaled to fit while keeping its aspect ratio.
    sections_per_page : int
        1 (default, one plot per sheet) or 4 (2x2 grid of plots).
    """

    def __init__(self, path, page="A3", landscape=True, title_block=None,
                 logo_path=None, sections_per_page=1):
        self.path = str(path)
        size = _PAGE_SIZES.get(str(page).upper(), pagesizes.A3)
        self.pagesize = pagesizes.landscape(size) if landscape else size
        if hasattr(title_block, "as_dict"):
            title_block = title_block.as_dict()
        self.title_block = dict(title_block or {})
        self.logo_path = logo_path
        if sections_per_page not in (1, 4):
            raise ValueError("sections_per_page must be 1 or 4.")
        self.sections_per_page = sections_per_page
        self._entries = []
        self._saved = False

    def add_profile_image(self, png_path, section_label, chainage_text,
                          extra_notes=None, scale_text=None):
        """Queue one section plot image for layout.

        ``extra_notes`` (e.g. a vertical exaggeration note) prints in small
        text beneath the image. ``scale_text`` (e.g. "HORIZ 1:500 | VE
        10×", computed by the caller) is drawn boxed at the lower-right of
        the sheet, just above the title block; None draws nothing. With
        four sections per page the first non-empty scale text in the
        page's batch is used. Pages are written by :meth:`save` once the
        total count is known (two-pass sheet numbering).
        """
        self._entries.append((str(png_path), str(section_label),
                              str(chainage_text),
                              None if extra_notes is None else str(extra_notes),
                              None if scale_text is None else str(scale_text)))

    def save(self):
        """Lay out all queued images and write the PDF; return the path."""
        if self._saved:
            return self.path
        c = pdf_canvas.Canvas(self.path, pagesize=self.pagesize)
        per_page = self.sections_per_page
        total = max(1, math.ceil(len(self._entries) / per_page))
        for sheet in range(total):
            batch = self._entries[sheet * per_page:(sheet + 1) * per_page]
            self._draw_page(c, batch, sheet + 1, total)
            c.showPage()
        c.save()
        self._saved = True
        logger.info("PDF written: %s (%d sheets)", self.path, total)
        return self.path

    # ------------------------------------------------------------------ #
    # page composition
    # ------------------------------------------------------------------ #

    def _draw_page(self, c, batch, sheet_no, total):
        """Draw border, title block and the image cells for one sheet."""
        width, height = self.pagesize
        c.setLineWidth(1.0)
        c.rect(_MARGIN, _MARGIN, width - 2 * _MARGIN, height - 2 * _MARGIN)
        self._draw_title_block(c, sheet_no, total)

        # content area above the title block
        x0 = _MARGIN
        y0 = _MARGIN + _TB_HEIGHT
        w = width - 2 * _MARGIN
        h = height - 2 * _MARGIN - _TB_HEIGHT
        if self.sections_per_page == 1:
            cells = [(x0, y0, w, h)]
        else:
            cw, ch = w / 2.0, h / 2.0
            cells = [(x0, y0 + ch, cw, ch), (x0 + cw, y0 + ch, cw, ch),
                     (x0, y0, cw, ch), (x0 + cw, y0, cw, ch)]
        for cell, entry in zip(cells, batch):
            self._draw_cell(c, cell, entry)

        scale_text = next((e[4] for e in batch if len(e) > 4 and e[4]),
                          None)
        if scale_text:
            self._draw_scale_box(c, scale_text)

    def _draw_scale_box(self, c, scale_text):
        """Boxed scale annotation at the lower-right, just above the
        title block strip (e.g. "HORIZ 1:500 | VE 10×")."""
        width, _height = self.pagesize
        c.setFont("Helvetica-Bold", 8)
        tw = c.stringWidth(scale_text, "Helvetica-Bold", 8)
        box_h = 6 * mm
        box_w = tw + 4 * mm
        x = width - _MARGIN - box_w - 2 * mm
        y = _MARGIN + _TB_HEIGHT + 2 * mm
        c.setLineWidth(0.5)
        c.rect(x, y, box_w, box_h)
        c.drawString(x + 2 * mm, y + 2 * mm, scale_text)

    def _draw_cell(self, c, cell, entry):
        """Draw one section image with its heading and optional notes."""
        png_path, label, chainage_text, notes = entry[:4]
        x, y, w, h = cell
        pad = 4 * mm
        heading_h = 7 * mm
        notes_h = 5 * mm if notes else 0

        c.setFont("Helvetica-Bold", 11)
        heading = f"{label}    {chainage_text}".strip()
        c.drawCentredString(x + w / 2.0, y + h - heading_h + 1.5 * mm,
                            heading)
        if notes:
            c.setFont("Helvetica", 7)
            c.drawCentredString(x + w / 2.0, y + pad * 0.4, notes)

        # image box, aspect preserved
        box_x = x + pad
        box_y = y + pad + notes_h
        box_w = w - 2 * pad
        box_h = h - heading_h - notes_h - 2 * pad
        if box_w <= 0 or box_h <= 0:
            return
        if not os.path.exists(png_path):
            c.setFont("Helvetica", 8)
            c.drawString(box_x, box_y + box_h / 2.0,
                         f"[image not found: {os.path.basename(png_path)}]")
            logger.warning("Section image missing: %s", png_path)
            return
        img = ImageReader(png_path)
        iw, ih = img.getSize()
        scale = min(box_w / float(iw), box_h / float(ih))
        dw, dh = iw * scale, ih * scale
        c.drawImage(img,
                    box_x + (box_w - dw) / 2.0,
                    box_y + (box_h - dh) / 2.0,
                    width=dw, height=dh,
                    preserveAspectRatio=True, mask="auto")

    def _draw_title_block(self, c, sheet_no, total):
        """Draw the labelled title block strip along the bottom border."""
        width, _height = self.pagesize
        x0 = _MARGIN
        y0 = _MARGIN
        strip_w = width - 2 * _MARGIN
        c.setLineWidth(1.0)
        c.line(x0, y0 + _TB_HEIGHT, x0 + strip_w, y0 + _TB_HEIGHT)

        fields_x = x0
        if self.logo_path and os.path.exists(str(self.logo_path)):
            self._draw_logo(c, x0, y0)
            fields_x = x0 + _LOGO_WIDTH
            c.line(fields_x, y0, fields_x, y0 + _TB_HEIGHT)

        rows = (0, 1)
        row_h = _TB_HEIGHT / float(len(rows))
        for row in rows:
            row_fields = [f for f in _TB_FIELDS if f[0] == row]
            cell_w = (x0 + strip_w - fields_x) / float(len(row_fields))
            cell_y = y0 + _TB_HEIGHT - (row + 1) * row_h
            for i, (_row, attr, label) in enumerate(row_fields):
                cx = fields_x + i * cell_w
                c.setLineWidth(0.5)
                c.rect(cx, cell_y, cell_w, row_h)
                c.setFont("Helvetica", 5)
                c.drawString(cx + 1.2 * mm,
                             cell_y + row_h - 2.4 * mm, label)
                if attr == "__sheet__":
                    value = f"Sheet {sheet_no} of {total}"
                else:
                    value = str(self.title_block.get(attr, "") or "")
                c.setFont("Helvetica-Bold", 8)
                c.drawString(cx + 1.2 * mm, cell_y + 1.8 * mm,
                             value[:60])

    def _draw_logo(self, c, x0, y0):
        """Place the logo in its box, keeping the image aspect ratio."""
        pad = 1.5 * mm
        box_w = _LOGO_WIDTH - 2 * pad
        box_h = _TB_HEIGHT - 2 * pad
        try:
            img = ImageReader(str(self.logo_path))
            iw, ih = img.getSize()
            scale = min(box_w / float(iw), box_h / float(ih))
            dw, dh = iw * scale, ih * scale
            c.drawImage(img,
                        x0 + pad + (box_w - dw) / 2.0,
                        y0 + pad + (box_h - dh) / 2.0,
                        width=dw, height=dh,
                        preserveAspectRatio=True, mask="auto")
        except Exception as exc:          # corrupt/unsupported logo file
            logger.warning("Could not draw logo '%s': %s",
                           self.logo_path, exc)
