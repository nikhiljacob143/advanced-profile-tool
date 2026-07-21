# Advanced Profile Tool

A QGIS plugin for alignment-based terrain analysis: generate cross-sections
along an alignment, sample multiple elevation rasters, compare terrain
surfaces, calculate cut/fill quantities, and export profiles and section
results in CAD and report formats.

- **Repository:** https://github.com/nikhiljacob143/advanced-profile-tool
- **Issue tracker:** https://github.com/nikhiljacob143/advanced-profile-tool/issues
- **License:** GNU GPL v2 or later (see `LICENSE`)
- **Minimum QGIS version:** 3.28

## Features

- Cross-section generation along a line alignment by **interval, count or an
  explicit chainage list** (typed, pasted, or loaded from CSV/XLSX), with
  optional sections at alignment vertices and station equations.
- Section direction control: local / averaged / smoothed tangent methods,
  vertex handling (angle bisector, incoming or outgoing segment), angle
  offset, fixed bearing and reversed normals.
- **Multi-DEM sampling** with nearest, bilinear or cubic interpolation and
  NoData-aware gap handling.
- Interactive **long-section and cross-section viewer** with vertical
  exaggeration, styling presets and section navigation.
- **Terrain comparison** between a reference and comparison surface, cut/fill
  areas per section and **average-end-area volumes** with configurable
  cut/fill convention.
- Processing algorithms (Extract Profiles, Generate Sections, Compare
  Surfaces) for use in models and batch runs.
- Exports: GeoPackage layers, CSV tables, Excel workbook, PNG/SVG plots,
  PDF section sheets, QGIS print layouts, and DXF (profile geometry,
  Civil-3D-style section sheets, and plan-view world-coordinate drawings).
- Timestamped, non-overwriting output folder management with a
  `run_parameters.json` manifest per run.

## Installation

Install from the QGIS Plugin Manager (`Plugins → Manage and Install
Plugins…`), or download a release ZIP and use `Install from ZIP`.

After installation the tool is available from the toolbar / `Plugins` menu
(`Ctrl+Alt+A`) and the Processing Toolbox under *Advanced Profile Tool*.

## Dependencies

| Package      | Required | Notes                                                                                     |
| ------------ | -------- | ----------------------------------------------------------------------------------------- |
| `numpy`      | Yes      | Ships with every QGIS installation.                                                        |
| `matplotlib` | Yes      | Bundled with the standard Windows/macOS installers and most Linux packages.                |
| `openpyxl`   | Optional | Only needed for **Excel export** and reading `.xlsx` chainage lists. Everything else works without it. |

If `openpyxl` is missing, the Excel option is shown disabled with an
explanatory tooltip. To install it, run in the QGIS Python environment
(e.g. the OSGeo4W shell on Windows):

```
python -m pip install openpyxl
```

then restart QGIS. DXF output uses a bundled (vendored) copy of `dxfwrite`;
no separate installation is needed.

## Quick start

1. Load a line layer (the alignment) and one or more elevation rasters.
2. Open the panel (`Ctrl+Alt+A`), pick the alignment feature and add DEMs.
3. Choose a section mode (interval / count / chainage list), widths and
   sampling settings.
4. Click **Generate** to build sections and sample the DEMs, then browse
   the long section and cross-sections in the viewer.
5. Optionally enable comparison/volumes, then choose export formats and an
   output folder and click **Export**.

Sample data: any DEM (e.g. an SRTM tile) plus a digitised line across it is
enough to test all features; use two DEMs (e.g. design vs existing surface)
to test comparison and volumes.

## Known limitations

- Vertex handling (incoming/outgoing) applies to the **local** tangent
  method; the averaged/smoothed methods blend directions across vertices by
  design.
- Per-section Excel sheets are limited to 200 sections (workbook size);
  larger runs fall back to the consolidated workbook.
- Alignments must be single-part lines; multi-part geometries use the
  longest part.
- CRS: the alignment and DEMs are reprojected on the fly, but a projected
  CRS in metres is recommended for meaningful chainages and volumes.

## Testing

The `core/` and `export/` engine modules are pure Python (no `qgis`
imports) and can be exercised headless:

```bash
python -m compileall advanced_profile_tool           # syntax check
```

For a functional check inside QGIS: generate sections on a small DEM,
verify chainage labels, run comparison + volumes, then export every
format and open the results.

## Reporting issues

Please report bugs and feature requests at
https://github.com/nikhiljacob143/advanced-profile-tool/issues and include the
QGIS version, OS, and the log messages from the *AdvancedProfileTool* panel.
