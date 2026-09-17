# AgroSuite

A local application for working with agricultural monitor data: import what
comes off the machine, clean harvest and application maps with a report of
what was removed, analyse strip trials the DIFM way, and generate the files
to take back to the monitor — already laid out the way each terminal expects.

Everything runs on your computer. No data leaves the machine: the server
listens only on `127.0.0.1` and the files live in a session folder.

![workflow](docs/workflow.svg)

## Installing on Windows

1. Install [Python 3.10 or newer](https://www.python.org/downloads/), ticking
   **Add Python to PATH** during installation.
2. Download or clone this folder.
3. Double-click **`run.bat`**.

The first run creates the environment and installs the dependencies (a few
minutes). After that the app opens straight in your browser.

On Linux or macOS, use `./run.sh`. On any system this also works:

```
pip install -r requirements.txt
python -m agrosuite
```

## Units

The app opens in the **Canadian default**: bu/ac for grain, lb/ac for
fertilizer and seed, acres, feet, mph and Canadian dollars. You can switch
the whole set from the picker at the top (Canada, United States, Brazil,
metric) or adjust each quantity through the ⚙ button.

Internally everything is stored in metric — kg/ha, hectares, metres, km/h.
Conversion happens only at display and at write time, so switching from acres
to hectares changes no stored number and requires no reload.

Bushel units depend on the crop, because a bushel measures volume: a bushel
of canola weighs 22.68 kg and one of wheat 27.22 kg. Pick the crop in the
same panel.

## The five steps

### 1 · Data

It takes what the monitor produces:

| Source | Format |
|---|---|
| John Deere | Operations Center shapefile and CSV, the USB zip, GS2/GS3/Gen 4 cards |
| Ag Leader / SMS | shapefile and CSV |
| Raven Viper 4 | job CSV, shapefile |
| Trimble GFX / TMX / FmX | coverage CSV, shapefile |
| Case IH AFS, New Holland PLM | ISOXML (TASKDATA plus binary TLG logs) |
| Bourgault X30/X35, Väderstad, Topcon/Müller | ISOXML, CSV |
| Augmenta | session GeoJSON, carrying vigour and rate |
| Anything else | shapefile, GeoJSON, CSV/TXT, Excel, KML/KMZ, ZIP |

The app identifies the manufacturer from the column signature and the folder
structure, converts everything to one schema, and reconstructs what is
missing — speed and heading come from the track itself when the file does not
carry them.

A shapefile or an ISOXML folder needs all of its files together: use **Open
by path / folder**, or upload a `.zip`.

### 2 · Cleaning

Thirteen chained filters, with a starting profile per operation type:

- null and non-positive values, inconsistent positions, moisture out of range;
- speed outside the operating range, and sharp speed changes;
- partial swath, short passes, pass starts and ends;
- **swath overlap** — the main source of false low values in a yield map;
- **field edge**, measured by a distance transform over the worked area, which
  follows irregularly shaped fields;
- global and local outliers.

Before the filters comes the **flow delay** correction: several seconds pass
between the cut and the sensor, and without shifting the series the whole map
ends up several metres out of place.

Nothing is overwritten. Cleaning produces two new datasets — *clean* and
*removed*, the latter carrying the reason for each discard — plus a report
with before and after, an overlaid histogram, and what each filter took out.

### 3 · DIFM analysis

It aggregates the points into cells (never mixing rates from neighbouring
strips), drops the transition between treatments, fits four response models —
quadratic, quadratic plateau, linear plateau and Mitscherlich — and picks the
one with the best R².

Given a crop price and an input cost, it computes the **economic optimum
rate**: the point where the next unit of input stops paying for itself. Given
a zone column, it fits one curve per zone and compares variable rate profit
against the best single rate — which is the number that decides whether the
map is worth building.

### 4 · Trial layout

It generates randomized block strips over the field boundary: width a
multiple of the implement, rates drawn within each block, direction aligned to
the longest side. It also generates the **AB line** along the strip
direction — without it the operator enters at another angle and the trial is
lost.

### 5 · Export

Two modes.

**Ready-to-load package** builds the USB folder in the layout the chosen
platform looks for, with boundary, AB lines and prescription, plus a
`README.txt` naming the import path. Whatever the platform does not accept is
left out and reported — better to know here than in the cab.

**Individual files** generates only what you ask for, with no folder
structure.

## Check before you take it out

Every package goes through an automatic check, run **over the files already
written** rather than over what was meant to be written. It covers the known
causes of rejection, one by one:

- shapefile: `.shp`/`.shx`/`.dbf`/`.prj` present, polygon geometry, valid
  polygons, projection, field names within the DBF limit, rate field present,
  numeric, with no nulls and no negatives, plausible magnitude, character
  encoding declared, size and feature count;
- ISOXML: folder and file named exactly, valid XML, closed rings, AB lines
  carrying both reference points, grid binary length matching what the header
  declares, cells carrying a rate, DDI declared.

The result comes in three levels: **checked**, **to confirm on the monitor
screen** and **blocker**. Nothing claims "this will work" — the check claims
that the known causes of failure have been ruled out. Firmware version and
import menus cannot be tested from here.

## What is proprietary and what is not

AgroSuite reads and writes open formats: shapefile, GeoJSON, CSV, KML and
ISOXML (ISO 11783-10), the standard of ISOBUS terminals.

It does not write proprietary formats. The setup files inside `GS2_2600/SETUP`
or `GS3_2630/SETUP`, along with `.gsd`, `.fdd`, `.jdf`, `.vy1` and the like,
are closed, and reverse-engineering them would produce files the display
refuses out in the field.

What the app does with them: it **inventories the card**, says what each file
is, reads every layer in an open format inside it — boundaries, lines,
shapefile prescriptions — and explains the conversion route when there is
nothing readable. A boundary read off a GreenStar card can be re-exported to
any other monitor without redrawing anything.

## Development

```
python -m pytest tests/ -q          # 67 tests
python tests/fixtures.py samples    # sample files for every monitor
python -m agrosuite --reload        # server with auto-reload
```

The tests run against files that mimic each platform's real export — same
column names, same units, same folder structure. If a manufacturer changes a
column name, the matching test breaks and the alias is updated in one place.

### Layout

```
agrosuite/
  core/       data model, column schema, units, CRS, AB lines
  formats/    per-format reading and writing, monitor identification,
              per-platform packages, verification
  clean/      cleaning filters and report
  difm/       response models, economics, trial layout
  app/        local server and interface
```

## Licence

MIT.
