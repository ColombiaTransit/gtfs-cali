# Metro Cali GTFS pipeline

Downloads, repairs, and validates the GTFS feed published by Metro Cali S.A.
(SITM MIO, Santiago de Cali, Colombia) on ArcGIS Hub:

- Item: https://indicadores-y-metas-del-pdm-2020-2023-metrocali.hub.arcgis.com/maps/163af9c688444778b74150cd84f64a8b/about
- Item ID: `163af9c688444778b74150cd84f64a8b`

## Why this isn't a simple `wget`

The item's own metadata states the data is **not** distributed as a single
`.zip`. Digging into the actual FeatureServer
(`https://services9.arcgis.com/8rJ42n9yWry0I4K4/arcgis/rest/services/GTFS/FeatureServer`)
shows it's not even "one layer per GTFS file" — it's a **relational
vehicle-schedule model** (the kind produced by scheduling software like
Trapeze/HASTUS/Optibus), with 9 layers/tables:

| Layer/Table | What it holds |
|---|---|
| `Stops` (0) | stop points — **no `stop_name` field** |
| `LineVariantElements` (1) | polyline segments between consecutive stops for a route pattern |
| `Calendars` (2) | service calendars |
| `CalendarExceptions` (3) | calendar exceptions |
| `Lines` (4) | routes — **no name fields**, just id + route_type |
| `LineVariants` (5) | links a Line to a direction + shape + stop pattern |
| `Runs` (6) | one row per actual scheduled trip |
| `ScheduleElements` (7) | timing **offsets** (not absolute times) for a pattern |
| `Schedules` (8) | links a Run to its timing pattern |

`reconstruct.py` contains the full join logic that turns this into standard
GTFS tables (trips = pattern + calendar + a start-time offset; stop_times =
`Runs.StartRun` + `ScheduleElements` offset, aligned to the stop sequence
from `LineVariantElements`). It's pure logic with no network calls, and is
covered by `test_reconstruct.py` (fabricated data matching the real schema
field-for-field) — **run that first** to see the join logic proven out
before trusting a live run:

```bash
python test_reconstruct.py
```

### Two things this schema forces us to auto-detect

Nothing in the schema states these explicitly, so `download.py` samples the
live data and infers them (`reconstruct.detect_schedule_alignment` /
`reconstruct.detect_time_unit`), printing `[diagnostic]` lines either way:

1. **Whether `ScheduleElements` has one row per stop or one row per
   segment** — inferred by comparing row counts against each pattern's stop
   count, majority vote across all patterns.
2. **Whether `StartRun`/`Arrival`/`Departure` are hours, minutes, or
   seconds** — inferred from the 95th-percentile "end of day" value.

If detection is ambiguous, the script raises a clear error telling you
what to inspect and how to hard-pin the answer via `TIME_UNIT_OVERRIDE` /
`SCHEDULE_ALIGNMENT_OVERRIDE` in `gtfs_common.py`. **On your first real
run, read the printed sample trips at the end of `download.py`'s output
and sanity-check them against any published Metro Cali schedule** (app,
website, PDF timetable) before trusting the feed.

### Known source data gaps

The source genuinely has no values for these — `fix.py` fills them with
loudly-logged placeholders so the feed is at least spec-valid, but you
should get the real values from Metro Cali (sistemas@metrocali.gov.co)
before treating this as a final, publishable feed:

- `stops.txt`: no `stop_name` → placeholder `"Parada <stop_id>"`
- `routes.txt`: no `route_short_name`/`route_long_name` → placeholder
  `route_short_name = route_id`
- No `Agency` table at all → a fixed record (`KNOWN_AGENCY` in
  `gtfs_common.py`) is used; double-check the URL/contact info there.

## Requirements

```bash
pip install -r requirements.txt
```

Needs normal internet access to `arcgis.com` / `services*.arcgis.com`
(not needed for `fix.py` / `validate.py`, which run entirely offline).

## Usage

```bash
python run_all.py
```

or run stages individually:

```bash
python download.py   # -> build/gtfs_raw/*.txt
python fix.py         # -> build/gtfs_clean/*.txt and build/gtfs.zip
python validate.py    # -> build/report/validation_report.txt
```

Final feed: **`build/gtfs.zip`**
Reports: `build/report/fix_report.txt`, `build/report/validation_report.txt`

## What each stage does

### 1. `download.py`
- Resolves the ArcGIS item -> underlying `FeatureServer` URL (falls back to
  the confirmed live URL in `gtfs_common.py` if item resolution fails).
- Fetches all 9 layers/tables by their known layer IDs (`gtfs_common.LAYER_IDS`).
- Extracts point geometry (`Stops`) and polyline geometry
  (`LineVariantElements`, used to build real `shapes.txt` with actual
  vertices and cumulative `shape_dist_traveled`).
- Hands everything to `reconstruct.py` to join into standard GTFS tables
  (see the schema explanation above).
- Auto-detects schedule alignment and time unit, prints diagnostics and a
  handful of fully-reconstructed sample trips for a sanity check.
- Writes raw `.txt` files to `build/gtfs_raw/`.

### 2. `fix.py`
Repairs the data-quality issues typical of ArcGIS-exported GTFS:
- Numeric IDs coming back as floats (`"1234.0"` → `"1234"`).
- Whitespace/encoding cleanup, forces UTF-8.
- Lat/lon out of range or swapped (auto-corrects obvious swaps, drops the
  rest, logs the difference).
- Date fields (epoch-millis, ISO, `DD/MM/YYYY`, etc.) → GTFS `YYYYMMDD`.
- Time fields → GTFS `H:MM:SS`/`HH:MM:SS` (allows times past 24:00:00 for
  post-midnight trips).
- Invalid/missing `route_type` → defaults to `3` (bus), since MIO is a
  bus/BRT system.
- Duplicate primary keys → keeps first, logs count.
- **Referential integrity**: drops `trips` pointing at unknown
  `route_id`/`service_id`, drops `stop_times` pointing at unknown
  `trip_id`/`stop_id`, drops trips left with zero stop_times, prunes
  `shapes.txt` points for shape_ids no trip uses.
- Sorts `stop_times.txt` by `(trip_id, stop_sequence)` and `shapes.txt` by
  `(shape_id, shape_pt_sequence)`.
- Flags (but does not fabricate) `calendar.txt` service spans shorter than
  7 days — the feed's own published terms of use require at least 7 days
  of validity, ideally 30.
- Synthesizes `feed_info.txt` if the source doesn't provide one.
- Zips everything at the **root** of the archive (no nested folder), as
  required by the GTFS spec.
- Writes `build/report/fix_report.txt` listing every change made.

### 3. `validate.py`
An independent, dependency-light checker (no assumptions carried over from
`fix.py`) that re-verifies the final `build/gtfs.zip`:
- Required files present (`agency`, `stops`, `routes`, `trips`,
  `stop_times`, and at least one of `calendar`/`calendar_dates`).
- Required fields non-empty.
- Primary-key uniqueness.
- Full foreign-key graph: `routes.agency_id`, `trips.route_id`,
  `trips.service_id`, `stop_times.trip_id`, `stop_times.stop_id`,
  `trips.shape_id`.
- Time format + per-trip `stop_sequence` monotonicity, every trip has ≥2
  stop_times.
- Date format + calendar-span warnings (7-day / 30-day thresholds).
- Coordinate range + a generous Cali bounding-box sanity check.
- `route_type` enum validity.

Writes `build/report/validation_report.txt` and exits with a clear
PASS/FAIL summary. **This is a fast first-pass check, not a replacement**
for the canonical validator — see below.

## Recommended second opinion: the canonical GTFS validator

For a fully spec-compliant, authoritative validation (the same one Google
and most transit tooling use), also run MobilityData's validator against
`build/gtfs.zip`:

```bash
# needs Java 17+
curl -L -o gtfs-validator-cli.jar \
  https://github.com/MobilityData/gtfs-validator/releases/latest/download/gtfs-validator-cli.jar
java -jar gtfs-validator-cli.jar --input build/gtfs.zip --output_base build/report/canonical
```

That produces an HTML/JSON report with the full official rule set
(notices, warnings, errors) — open `build/report/canonical/report.html`.

## If detection or a join looks wrong

- Re-run `python test_reconstruct.py` — if that fails, the join logic
  itself is broken and layer field names have probably changed upstream.
- If `test_reconstruct.py` passes but a live run's sample trips look wrong
  (nonsensical times, stops out of order), it's almost certainly the
  alignment/unit auto-detection guessing wrong on real data — check the
  `[diagnostic]` vote tallies `download.py` prints, and hard-pin
  `TIME_UNIT_OVERRIDE` / `SCHEDULE_ALIGNMENT_OVERRIDE` in `gtfs_common.py`
  accordingly.
- If Metro Cali changes the underlying schema (renames a field, adds a
  layer), update the corresponding `build_*` function in `reconstruct.py`
  and its test in `test_reconstruct.py` together.

## GitHub Actions

`.github/workflows/gtfs-pipeline.yml` automates the whole thing:

- **Triggers**: daily cron (`0 4 * * *` UTC — edit to taste) **and**
  manual `workflow_dispatch`.
- **Steps**: `download.py` → `fix.py` → downloads the latest
  [MobilityData `gtfs-validator-cli.jar`](https://github.com/MobilityData/gtfs-validator)
  → runs it against `build/gtfs.zip` → `check_validator_report.py` fails the
  job if any `ERROR`-severity notices are present.
- **On success**: copies the feed to `data/gtfs.zip` and the validator
  report to `data/validation/` and commits them straight to the repo (only
  if something actually changed — no empty commits). This uses the default
  `GITHUB_TOKEN`, so no extra secrets are needed; the workflow's
  `permissions: contents: write` is what allows the push.
- **On failure**: nothing is committed, and the full validator report is
  still uploaded as a workflow artifact (`gtfs-validation-report`) so you
  can inspect it from the Actions run.
- There's a commented-out `softprops/action-gh-release` step at the bottom
  for when you're ready to also publish GitHub Releases — just uncomment it.

To drop this into your existing repo:

```bash
cp -r gtfs_pipeline/*.py gtfs_pipeline/requirements.txt <your-repo>/gtfs_pipeline/
cp -r gtfs_pipeline/.github <your-repo>/
```

(or adjust `PIPELINE_DIR` / `OUTPUT_ZIP_PATH` / `OUTPUT_REPORT_DIR` at the
top of the workflow file if you'd rather use different paths.)

Note: this workflow deliberately does **not** run `validate.py` (our
custom quick checker) in CI — only the canonical MobilityData validator
gates the commit, per your call. `validate.py` is still there for fast
local iteration while you're debugging a layer-mapping issue, before
waiting on the Java-based canonical run.

## Notes on the source data

- Publisher: Metro Cali S.A. — sistemas@metrocali.gov.co
- License: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)
- Feed vigencia (validity): 2026
- The publisher explicitly states data must be valid ≥7 days, ideally
  covering ≥30 days of service — `fix.py`/`validate.py` check this.
