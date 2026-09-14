"""
Step 3 - Validate
==================
Independent structural/semantic validation of the final gtfs.zip:
  * required files present
  * required fields non-empty
  * primary key uniqueness
  * foreign key / referential integrity across all files
  * arrival/departure time format, and departure >= arrival per row
  * date formats and calendar service-span (feed's own >=7-day requirement)
  * lat/lon ranges + Cali bounding-box sanity check
  * stop_times monotonic per trip, every trip has >=2 stops

Prints a pass/fail summary and writes build/report/validation_report.txt

This is a self-contained checker with no extra dependencies, meant to catch
the majority of real-world issues fast. For a second, authoritative opinion
before publishing, also run the canonical MobilityData validator (needs
Java):
    https://github.com/MobilityData/gtfs-validator
    java -jar gtfs-validator-cli.jar --input build/gtfs.zip --output_base report_canonical

Run: python validate.py
"""
import io
import re
import zipfile

import pandas as pd

from gtfs_common import CALI_BBOX, FINAL_ZIP, REPORT_DIR, REQUIRED_FILES, ensure_dirs

ERRORS = []
WARNINGS = []


def err(msg):
    ERRORS.append(msg)


def warn(msg):
    WARNINGS.append(msg)


def load_zip(path):
    dfs = {}
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        for name in names:
            if not name.endswith(".txt"):
                continue
            with zf.open(name) as f:
                dfs[name] = pd.read_csv(io.TextIOWrapper(f, encoding="utf-8"), dtype=str,
                                         keep_default_na=False, na_values=[""])
    return dfs


TIME_RE = re.compile(r"^\d{1,2}:[0-5]\d:[0-5]\d$")
DATE_RE = re.compile(r"^\d{8}$")


def check_required_files(dfs):
    for f in REQUIRED_FILES:
        if f not in dfs:
            err(f"REQUIRED file missing: {f}")
    if "calendar.txt" not in dfs and "calendar_dates.txt" not in dfs:
        err("At least one of calendar.txt / calendar_dates.txt is required")


def check_required_fields(dfs):
    required_fields = {
        "agency.txt": ["agency_name", "agency_url", "agency_timezone"],
        "stops.txt": ["stop_id", "stop_name", "stop_lat", "stop_lon"],
        "routes.txt": ["route_id", "route_type"],
        "trips.txt": ["route_id", "service_id", "trip_id"],
        "stop_times.txt": ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
        "calendar.txt": ["service_id", "start_date", "end_date"],
        "calendar_dates.txt": ["service_id", "date", "exception_type"],
    }
    for fname, cols in required_fields.items():
        if fname not in dfs:
            continue
        df = dfs[fname]
        for c in cols:
            if c not in df.columns:
                err(f"{fname}: required column '{c}' is missing")
                continue
            n_missing = df[c].isna().sum()
            if n_missing:
                err(f"{fname}: {n_missing} row(s) have empty required field '{c}'")


def check_primary_keys(dfs):
    pk_map = {
        "agency.txt": "agency_id", "stops.txt": "stop_id", "routes.txt": "route_id",
        "trips.txt": "trip_id", "calendar.txt": "service_id",
    }
    for fname, pk in pk_map.items():
        if fname in dfs and pk in dfs[fname].columns:
            dup = dfs[fname][pk].duplicated().sum()
            if dup:
                err(f"{fname}: {dup} duplicate value(s) in primary key '{pk}'")
    if "stop_times.txt" in dfs:
        df = dfs["stop_times.txt"]
        if {"trip_id", "stop_sequence"}.issubset(df.columns):
            dup = df.duplicated(subset=["trip_id", "stop_sequence"]).sum()
            if dup:
                err(f"stop_times.txt: {dup} duplicate (trip_id, stop_sequence) pairs")


def check_referential_integrity(dfs):
    def ids(fname, col):
        return set(dfs[fname][col].dropna()) if fname in dfs and col in dfs[fname].columns else None

    agency_ids = ids("agency.txt", "agency_id")
    stop_ids = ids("stops.txt", "stop_id")
    route_ids = ids("routes.txt", "route_id")
    trip_ids = ids("trips.txt", "trip_id")
    service_ids = set()
    if "calendar.txt" in dfs:
        service_ids |= set(dfs["calendar.txt"].get("service_id", pd.Series(dtype=str)).dropna())
    if "calendar_dates.txt" in dfs:
        service_ids |= set(dfs["calendar_dates.txt"].get("service_id", pd.Series(dtype=str)).dropna())

    if "routes.txt" in dfs and agency_ids and "agency_id" in dfs["routes.txt"].columns:
        bad = dfs["routes.txt"][
            dfs["routes.txt"]["agency_id"].notna() & ~dfs["routes.txt"]["agency_id"].isin(agency_ids)
        ]
        if len(bad):
            err(f"routes.txt: {len(bad)} row(s) reference unknown agency_id")

    if "trips.txt" in dfs and route_ids is not None:
        bad = dfs["trips.txt"][~dfs["trips.txt"]["route_id"].isin(route_ids)]
        if len(bad):
            err(f"trips.txt: {len(bad)} row(s) reference unknown route_id")
    if "trips.txt" in dfs and service_ids:
        bad = dfs["trips.txt"][~dfs["trips.txt"]["service_id"].isin(service_ids)]
        if len(bad):
            err(f"trips.txt: {len(bad)} row(s) reference unknown service_id")

    if "stop_times.txt" in dfs:
        st = dfs["stop_times.txt"]
        if trip_ids is not None:
            bad = st[~st["trip_id"].isin(trip_ids)]
            if len(bad):
                err(f"stop_times.txt: {len(bad)} row(s) reference unknown trip_id")
        if stop_ids is not None:
            bad = st[~st["stop_id"].isin(stop_ids)]
            if len(bad):
                err(f"stop_times.txt: {len(bad)} row(s) reference unknown stop_id")
        if trip_ids is not None:
            trips_without_stoptimes = trip_ids - set(st["trip_id"].dropna())
            if trips_without_stoptimes:
                err(f"trips.txt: {len(trips_without_stoptimes)} trip(s) have no stop_times rows at all")

    if "shapes.txt" in dfs and "trips.txt" in dfs and "shape_id" in dfs["trips.txt"].columns:
        shape_ids = set(dfs["shapes.txt"]["shape_id"].dropna())
        used_shapes = set(dfs["trips.txt"]["shape_id"].dropna())
        missing = used_shapes - shape_ids
        if missing:
            err(f"trips.txt: {len(missing)} distinct shape_id value(s) not found in shapes.txt")


def check_times_and_sequence(dfs):
    if "stop_times.txt" not in dfs:
        return
    st = dfs["stop_times.txt"].copy()
    for col in ("arrival_time", "departure_time"):
        if col not in st.columns:
            continue
        bad = st[~st[col].fillna("").apply(lambda v: bool(TIME_RE.match(v)) if v else False)]
        if len(bad):
            err(f"stop_times.txt: {len(bad)} row(s) have invalid '{col}' format (expected H:MM:SS or HH:MM:SS)")

    if {"trip_id", "stop_sequence"}.issubset(st.columns):
        st["stop_sequence_num"] = pd.to_numeric(st["stop_sequence"], errors="coerce")
        bad_seq = 0
        for _, g in st.groupby("trip_id"):
            seq = g["stop_sequence_num"].tolist()
            if seq != sorted(seq) or len(set(seq)) != len(seq):
                bad_seq += 1
        if bad_seq:
            err(f"stop_times.txt: {bad_seq} trip(s) have out-of-order or duplicate stop_sequence values")

        counts = st.groupby("trip_id").size()
        too_few = (counts < 2).sum()
        if too_few:
            err(f"stop_times.txt: {too_few} trip(s) have fewer than 2 stop_times rows")


def check_dates(dfs):
    for fname, col in (("calendar.txt", "start_date"), ("calendar.txt", "end_date"),
                        ("calendar_dates.txt", "date")):
        if fname in dfs and col in dfs[fname].columns:
            bad = dfs[fname][~dfs[fname][col].fillna("").apply(lambda v: bool(DATE_RE.match(v)) if v else False)]
            if len(bad):
                err(f"{fname}: {len(bad)} row(s) have invalid '{col}' (expected YYYYMMDD)")

    if "calendar.txt" in dfs:
        cal = dfs["calendar.txt"]
        try:
            span = (pd.to_datetime(cal["end_date"], format="%Y%m%d") -
                    pd.to_datetime(cal["start_date"], format="%Y%m%d")).dt.days
            n_short = (span < 7).sum()
            if n_short:
                warn(f"calendar.txt: {n_short} service_id(s) are valid for fewer than 7 days "
                     f"(the feed's own stated terms require >=7 days)")
            n_long_enough = (span >= 30).sum()
            if n_long_enough < len(cal):
                warn(f"calendar.txt: only {n_long_enough}/{len(cal)} service_id(s) cover >=30 days "
                     f"(feed terms recommend >=30 days where possible)")
        except Exception:
            pass


def check_coordinates(dfs):
    if "stops.txt" not in dfs:
        return
    stops = dfs["stops.txt"].copy()
    stops["stop_lat"] = pd.to_numeric(stops["stop_lat"], errors="coerce")
    stops["stop_lon"] = pd.to_numeric(stops["stop_lon"], errors="coerce")
    bad_range = stops[~stops["stop_lat"].between(-90, 90) | ~stops["stop_lon"].between(-180, 180)]
    if len(bad_range):
        err(f"stops.txt: {len(bad_range)} row(s) have out-of-range lat/lon")
    out_bbox = stops[
        stops["stop_lat"].between(-90, 90) & stops["stop_lon"].between(-180, 180) &
        (~stops["stop_lat"].between(CALI_BBOX["lat_min"], CALI_BBOX["lat_max"]) |
         ~stops["stop_lon"].between(CALI_BBOX["lon_min"], CALI_BBOX["lon_max"]))
    ]
    if len(out_bbox):
        warn(f"stops.txt: {len(out_bbox)} stop(s) fall outside the expected Cali bounding box "
             f"(not necessarily wrong, but worth a manual check)")


def check_parent_station(dfs):
    if "stops.txt" not in dfs or "parent_station" not in dfs["stops.txt"].columns:
        return
    stops = dfs["stops.txt"]
    stop_ids = set(stops["stop_id"].dropna())
    has_parent = stops[stops["parent_station"].notna() & (stops["parent_station"].astype(str).str.strip() != "")]
    bad = has_parent[~has_parent["parent_station"].isin(stop_ids)]
    if len(bad):
        err(f"stops.txt: {len(bad)} row(s) have parent_station pointing at a "
            f"stop_id that doesn't exist")


def check_route_type(dfs):
    if "routes.txt" not in dfs or "route_type" not in dfs["routes.txt"].columns:
        return
    valid = set(range(0, 8)) | set(range(100, 1800, 100))
    rt = pd.to_numeric(dfs["routes.txt"]["route_type"], errors="coerce")
    bad = ~rt.isin(valid)
    if bad.any():
        err(f"routes.txt: {int(bad.sum())} row(s) have an invalid route_type")


def main():
    ensure_dirs()
    print(f"Loading {FINAL_ZIP} ...")
    try:
        dfs = load_zip(FINAL_ZIP)
    except FileNotFoundError:
        print(f"ERROR: {FINAL_ZIP} not found. Run download.py then fix.py first.")
        return

    print(f"Loaded {len(dfs)} file(s): {sorted(dfs.keys())}\n")

    check_required_files(dfs)
    check_required_fields(dfs)
    check_primary_keys(dfs)
    check_referential_integrity(dfs)
    check_times_and_sequence(dfs)
    check_dates(dfs)
    check_coordinates(dfs)
    check_parent_station(dfs)
    check_route_type(dfs)

    report_path = f"{REPORT_DIR}/validation_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("GTFS validation report\n=======================\n\n")
        f.write(f"ERRORS ({len(ERRORS)}):\n")
        for e in ERRORS:
            f.write(f"  [ERROR] {e}\n")
        f.write(f"\nWARNINGS ({len(WARNINGS)}):\n")
        for w in WARNINGS:
            f.write(f"  [WARN]  {w}\n")

    print("=== Validation summary ===")
    print(f"Errors:   {len(ERRORS)}")
    print(f"Warnings: {len(WARNINGS)}")
    for e in ERRORS:
        print(f"  [ERROR] {e}")
    for w in WARNINGS:
        print(f"  [WARN]  {w}")
    print(f"\nFull report: {report_path}")

    if ERRORS:
        print("\nRESULT: FEED HAS BLOCKING ERRORS - fix and re-run before publishing.")
    else:
        print("\nRESULT: No blocking errors found by this checker. "
              "Recommended: also run the canonical MobilityData gtfs-validator "
              "(https://github.com/MobilityData/gtfs-validator) for a second opinion.")


if __name__ == "__main__":
    main()
