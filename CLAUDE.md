# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
python -m pytest tests/ -q                      # full suite, ~5 minutes
python -m pytest tests/test_terrain_grid.py -q  # one file
python -m pytest tests/ -q -k "depression"      # one test by name
python -m agrosuite --reload                    # dev server, auto-reload
python -m agrosuite --port 8765 --no-browser    # fixed port, no browser
python tests/fixtures.py samples                # write sample monitor files
```

`run.bat` (Windows) and `run.sh` build the venv on first use and reinstall when
`requirements.txt` changes; they then run `python -m agrosuite`.

Browser checks use Playwright with the preinstalled Chromium:
`sync_playwright().chromium.launch(executable_path="/opt/pw-browsers/chromium")`.
Basemap tiles are blocked in sandboxes, so ignore console errors containing
`ERR_CERT`.

Set `AGROSUITE_HOME` to a temp directory and `AGROSUITE_AUTOSAVE=off` before
running anything that touches the app outside the suite — `tests/conftest.py`
already does both, and without them a test run writes into the real
`~/Documents/AgroSuite`.

## Architecture

A local FastAPI server on `127.0.0.1` with a vanilla-JS front end. No build
step, no framework, no bundler: `agrosuite/app/static/` is served as it is.

**The data model is one schema.** Every reader — shapefile, CSV, ISOXML, John
Deere card, Augmenta GeoJSON, DEM GeoTIFF — produces a `Dataset`
(`core/dataset.py`): a pandas DataFrame whose columns are the canonical names
in `core/schema.py`, plus `DatasetMeta` and an optional shapely geometry list.
`core/schema.py` holds the manufacturer alias tables that map a file's column
names onto those; adding support for a monitor usually means adding aliases,
not a reader.

**Units: metric inside, always.** kg/ha, hectares, metres, km/h, kg. Nothing
downstream ever sees another unit. `core/units.py` converts at display and at
write time only, and `UNIT_PRESETS['canada']` is the default.

**Prose converts too, and this is the rule most easily broken.** A finding is a
sentence with its numbers written into it, so it cannot be converted the way a
table can — it has to be *written again*. Any function that produces text a
person reads takes a `units` parameter (default `None` = metric) and renders
every quantity through `units.Phrase`. The display unit set lives on the
session (`Session.display_units`, set by `PUT /api/units/display`), and
`server._restated(entry, kind)` rewrites a stored report's sentences on every
read. Remarks made while a file is being read are stored as *facts*
(`terrain/notes.py`: `{"key": "smoothing", "smooth_m": 10.0, ...}`) with one
writer per key, for the same reason. **Never format a unit into a string with
an f-string.**

**Stored numbers are never rewritten.** Reports are stored as made; only the
prose is regenerated. A project saved in acres opens in hectares with no
migration.

### Layout

| Path | What lives there |
|---|---|
| `core/` | schema, units, `Dataset`, CRS, first look (`preflight.py`), guided flow (`workflow.py`), machine profiles |
| `formats/` | one module per family; `registry.py` detects and dispatches, `writers.py`/`packages.py` build monitor packages, `validate.py` checks them, `raster.py` reads DEMs |
| `clean/` | 13 chained filters (`steps.py`) and the pipeline that reports what each removed |
| `difm/` | response models, economics, trial layout, layer joining. **Internal name only** — on screen this is "Economics" |
| `terrain/` | elevation grid, derivatives, hydrology, landforms, contours, rendering, yield-against-relief |
| `app/` | server, session, persistence, auto-save, project lock, routes, static front end |
| `mcp_server.py` | the tool surface Claude drives, over HTTP against the running app |

### Server and session

`app/server.py` holds the core endpoints and the module-level `state`
(`app/session.py`). Feature routers are auto-discovered: drop a module with a
module-level `router` into `app/routes/` and it is included. Inside handlers,
import the server lazily (`from agrosuite.app import server as server_mod`) —
it is partially initialised while routers load.

`server._register(dataset, label, origin, parent_id)` is the choke point for
every import: it registers the dataset, runs the first look and assigns a
role. **A `parent_id` makes the new dataset inherit and remove the parent's
project role**, so pass it only for a dataset that replaces its parent.

`server._fail(msg, status)` raises the HTTP error. Every route that changes
state must call `state.touch()` or auto-save will not notice it
(`tests/test_autosave.py` fails on any route that does not).

### Front end

`app.js` is one `App` object extended by `Object.assign(App, {...})` blocks.
Tabs render into `#right-panel` through a `renderers` map keyed off
`data-tab`, so a new tab is a button plus one entry. Shared helpers worth
reusing rather than reinventing: `this.api()`, `this.toast()`, `this.busy()`,
`missingPanel()` (what is missing plus the one button that fixes it),
`producesPanel()` ("if you stop here"), `callToAction()`, `bindPanelActions()`.
`units.js` mirrors `core/units.py` for display; `mapview.js` wraps Leaflet plus
a canvas point layer; `charts.js` is inline SVG.

### Wire names that must not change

The tab is "Economics" and the printed page is the "Economic report", but the
stored keys are older and a saved project depends on them: `reports['difm']`,
the goal key `'difm'`, `POST /api/datasets/{id}/difm`, the JS cache key
`` `${id}:difm` ``, the element ids `difm-*`, and the `agrosuite/difm/`
package. Each definition carries a comment saying so. Renaming any of them
breaks every `.agrosuite` file already written.

## Conventions

- Docstrings and comments explain **why**, not what. The codebase reads as
  prose and new code is expected to match.
- Error messages tell the user what to do next, naming the file or the setting.
- Nothing is silently overwritten: cleaning produces new datasets, saves keep a
  backup, an empty session never writes over a project.
- `tests/test_agrosuite.py` and `tests/fixtures.py` are the baseline — do not
  edit them except when a deliberate rename makes an assertion wrong, and say
  so. New tests go in a new `tests/test_<topic>.py`.
- No proprietary formats are written (`.gsd`, `.fdd`, `.jdf`, `.vy1`, GreenStar
  `SETUP` files). Cards are inventoried and their open layers read; that is
  deliberate, not a gap to fill.

## Honest limitations to preserve

The monitor readers and writers were built and tested against fixtures
constructed from the format specifications, not against real exports from a
customer's SMS, Viper 4 or Gen 4 display. Package validation rules out the
known causes of rejection; it cannot test a firmware version or an import
menu. Claims in the interface are phrased accordingly — keep them that way.
