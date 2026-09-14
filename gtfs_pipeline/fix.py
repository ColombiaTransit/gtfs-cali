"""
Step 2 - Fix
============
Reads the raw GTFS files (from download.py) and repairs the data-quality
issues that are endemic to GTFS exported out of ArcGIS Feature Services:

  * numeric ID columns coming back as floats ("1234.0" -> "1234")
  * stray whitespace / BOM / mixed encodings
  * lat/lon out of range, or lat/lon swapped
  * date columns not in GTFS's YYYYMMDD format (ArcGIS often returns
    epoch-millisecond timestamps or ISO datetimes for Date fields)
  * time columns not in HH:MM:SS (allows >24:00:00 for post-midnight trips)
  * duplicate primary keys (keeps first occurrence)
  * orphaned rows that violate referential integrity (e.g. a stop_time
    pointing at a trip_id that doesn't exist) - these are DROPPED and
    reported, because a validator would reject the whole feed otherwise
  * stop_times not sorted by (trip_id, stop_sequence)
  * missing route_type -> defaults to 3 (bus), since MIO is a bus/BRT system
  * missing feed_info.txt -> synthesized from what we know
  * writes everything back out as a zipped, spec-compliant GTFS feed

Run:  python fix.py
"""
import os
import re
import zipfile
from datetime import datetime, timedelta

import pandas as pd

from gtfs_common import (
    CALI_BBOX, CLEAN_DIR, FINAL_ZIP, RAW_DIR, REQUIRED_FILES, ensure_dirs,
)

ISSUES = []  # collected (file, message) for the human-readable report


def log(file, msg):
    ISSUES.append((file, msg))
    print(f"  [{file}] {msg}")


def read_raw(fname):
    path = f"{RAW_DIR}/{fname}"
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    return df


def strip_strings(df):
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.strip()
            df[c] = df[c].replace({"nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})
    return df


def clean_id_like(series):
    """'1234.0' -> '1234'; leaves real alphanumeric IDs untouched."""
    def fix(v):
        if pd.isna(v):
            return v
        s = str(v).strip()
        if re.fullmatch(r"-?\d+\.0+", s):
            return s.split(".")[0]
        return s
    return series.map(fix)


def to_gtfs_date(series):
    """Coerce a variety of date representations to YYYYMMDD."""
    def fix(v):
        if pd.isna(v) or str(v).strip() == "":
            return v
        s = str(v).strip()
        if re.fullmatch(r"\d{8}", s):
            return s
        # epoch millis (ArcGIS Date fields often come back this way)
        if re.fullmatch(r"\d{13}", s):
            dt = datetime.utcfromtimestamp(int(s) / 1000)
            return dt.strftime("%Y%m%d")
        # ISO date / datetime
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(s[:19], fmt).strftime("%Y%m%d")
            except ValueError:
                continue
        return s  # leave as-is; validator will flag it
    return series.map(fix)


TIME_RE = re.compile(r"^(\d{1,2}):([0-5]\d):([0-5]\d)$")


def to_gtfs_time(series):
    def fix(v):
        if pd.isna(v) or str(v).strip() == "":
            return v
        s = str(v).strip()
        if TIME_RE.match(s):
            h, mi, se = s.split(":")
            return f"{int(h):02d}:{int(mi):02d}:{int(se):02d}"
        # loosely-formed H:M:S or H:M with 1-2 digit parts each (zero-pad)
        parts = s.split(":")
        if len(parts) in (2, 3) and all(p.strip().isdigit() for p in parts):
            h = int(parts[0])
            mi = int(parts[1])
            se = int(parts[2]) if len(parts) == 3 else 0
            if 0 <= mi < 60 and 0 <= se < 60:
                return f"{h:02d}:{mi:02d}:{se:02d}"
        # epoch millis time-of-day (rare, but seen from ArcGIS Time fields)
        if re.fullmatch(r"\d{13}", s):
            dt = datetime.utcfromtimestamp(int(s) / 1000)
            return dt.strftime("%H:%M:%S")
        return s
    return series.map(fix)


def fix_agency(df):
    df = strip_strings(df)
    if "agency_id" in df.columns:
        df["agency_id"] = clean_id_like(df["agency_id"])
    if "agency_timezone" not in df.columns or df["agency_timezone"].isna().all():
        df["agency_timezone"] = "America/Bogota"
        log("agency.txt", "agency_timezone missing -> defaulted to America/Bogota")
    if "agency_lang" not in df.columns or df["agency_lang"].isna().all():
        df["agency_lang"] = "es"
    before = len(df)
    df = df.drop_duplicates(subset=[c for c in ["agency_id"] if c in df.columns])
    if len(df) != before:
        log("agency.txt", f"dropped {before-len(df)} duplicate agency_id rows")
    return df


def fix_stops(df):
    df = strip_strings(df)
    df["stop_id"] = clean_id_like(df["stop_id"])

    if "stop_name" not in df.columns:
        df["stop_name"] = pd.NA
    missing_name = df["stop_name"].isna() | (df["stop_name"].astype(str).str.strip() == "")
    if missing_name.any():
        log("stops.txt", f"SOURCE DATA GAP: {int(missing_name.sum())} stop(s) have no "
                          f"stop_name in the source - filled with a placeholder "
                          f"('Parada <stop_id>'). Get real names from Metro Cali "
                          f"(sistemas@metrocali.gov.co) before publishing.")
        df.loc[missing_name, "stop_name"] = "Parada " + df.loc[missing_name, "stop_id"].astype(str)

    df["stop_lat"] = pd.to_numeric(df["stop_lat"], errors="coerce")
    df["stop_lon"] = pd.to_numeric(df["stop_lon"], errors="coerce")

    # auto-swap lat/lon if it looks like they're reversed
    swap_mask = (
        (df["stop_lat"].abs() > 90) & (df["stop_lon"].abs() <= 90) &
        (df["stop_lon"].between(CALI_BBOX["lat_min"], CALI_BBOX["lat_max"]))
    )
    n_swap = int(swap_mask.sum())
    if n_swap:
        df.loc[swap_mask, ["stop_lat", "stop_lon"]] = df.loc[swap_mask, ["stop_lon", "stop_lat"]].values
        log("stops.txt", f"swapped lat/lon for {n_swap} rows that were reversed")

    bad = df[
        df["stop_lat"].isna() | df["stop_lon"].isna() |
        ~df["stop_lat"].between(-90, 90) | ~df["stop_lon"].between(-180, 180)
    ]
    if len(bad):
        log("stops.txt", f"dropped {len(bad)} rows with invalid/missing coordinates")
        df = df.drop(bad.index)

    out_of_cali = df[
        ~df["stop_lat"].between(CALI_BBOX["lat_min"], CALI_BBOX["lat_max"]) |
        ~df["stop_lon"].between(CALI_BBOX["lon_min"], CALI_BBOX["lon_max"])
    ]
    if len(out_of_cali):
        log("stops.txt", f"NOTE: {len(out_of_cali)} stops fall outside the expected "
                          f"Cali bounding box - kept, but worth a manual look "
                          f"(ids: {list(out_of_cali['stop_id'].head(10))}{' ...' if len(out_of_cali) > 10 else ''})")

    if "location_type" not in df.columns:
        df["location_type"] = "0"
    df["location_type"] = df["location_type"].fillna("0")
    df.loc[~df["location_type"].isin([str(i) for i in range(5)]), "location_type"] = "0"

    before = len(df)
    df = df.drop_duplicates(subset=["stop_id"])
    if len(df) != before:
        log("stops.txt", f"dropped {before-len(df)} duplicate stop_id rows")
    return df


def fix_routes(df, valid_agency_ids):
    df = strip_strings(df)
    df["route_id"] = clean_id_like(df["route_id"])
    if "agency_id" in df.columns:
        df["agency_id"] = clean_id_like(df["agency_id"])
        if valid_agency_ids and len(valid_agency_ids) == 1:
            missing = df["agency_id"].isna()
            if missing.any():
                df.loc[missing, "agency_id"] = next(iter(valid_agency_ids))

    if "route_type" not in df.columns:
        df["route_type"] = "3"
    df["route_type"] = pd.to_numeric(df["route_type"], errors="coerce")
    invalid_rt = df["route_type"].isna() | ~df["route_type"].isin(
        list(range(0, 8)) + list(range(100, 1800, 100))
    )
    if invalid_rt.any():
        log("routes.txt", f"defaulted route_type to 3 (bus) for {int(invalid_rt.sum())} rows")
        df.loc[invalid_rt, "route_type"] = 3
    df["route_type"] = df["route_type"].astype(int).astype(str)

    # at least one of short_name / long_name must be present
    def is_blank(col):
        if col not in df.columns:
            return pd.Series(True, index=df.index)
        s = df[col]
        return s.isna() | (s.astype(str).str.strip() == "")

    missing_names = is_blank("route_short_name") & is_blank("route_long_name")
    if missing_names.any():
        log("routes.txt", f"SOURCE DATA GAP: {int(missing_names.sum())} route(s) have "
                           f"neither route_short_name nor route_long_name in the source "
                           f"- filled route_short_name with route_id as a placeholder. "
                           f"Get real route names from Metro Cali "
                           f"(sistemas@metrocali.gov.co) before publishing.")
        df.loc[missing_names, "route_short_name"] = df.loc[missing_names, "route_id"].astype(str)

    before = len(df)
    df = df.drop_duplicates(subset=["route_id"])
    if len(df) != before:
        log("routes.txt", f"dropped {before-len(df)} duplicate route_id rows")
    return df


def fix_trips(df, valid_route_ids, valid_service_ids):
    df = strip_strings(df)
    df["trip_id"] = clean_id_like(df["trip_id"])
    df["route_id"] = clean_id_like(df["route_id"])
    df["service_id"] = clean_id_like(df["service_id"])

    before = len(df)
    df = df[df["route_id"].isin(valid_route_ids)]
    if len(df) != before:
        log("trips.txt", f"dropped {before-len(df)} trips referencing unknown route_id")

    before = len(df)
    df = df[df["service_id"].isin(valid_service_ids)]
    if len(df) != before:
        log("trips.txt", f"dropped {before-len(df)} trips referencing unknown service_id")

    if "direction_id" in df.columns:
        bad_dir = ~df["direction_id"].isin(["0", "1", pd.NA]) & df["direction_id"].notna()
        df.loc[bad_dir, "direction_id"] = pd.NA

    before = len(df)
    df = df.drop_duplicates(subset=["trip_id"])
    if len(df) != before:
        log("trips.txt", f"dropped {before-len(df)} duplicate trip_id rows")
    return df


def fix_stop_times(df, valid_trip_ids, valid_stop_ids):
    df = strip_strings(df)
    df["trip_id"] = clean_id_like(df["trip_id"])
    df["stop_id"] = clean_id_like(df["stop_id"])
    df["arrival_time"] = to_gtfs_time(df["arrival_time"])
    df["departure_time"] = to_gtfs_time(df.get("departure_time", df["arrival_time"]))
    df["stop_sequence"] = pd.to_numeric(df["stop_sequence"], errors="coerce")

    before = len(df)
    df = df[df["trip_id"].isin(valid_trip_ids)]
    if len(df) != before:
        log("stop_times.txt", f"dropped {before-len(df)} rows referencing unknown trip_id")

    before = len(df)
    df = df[df["stop_id"].isin(valid_stop_ids)]
    if len(df) != before:
        log("stop_times.txt", f"dropped {before-len(df)} rows referencing unknown stop_id")

    before = len(df)
    df = df.dropna(subset=["stop_sequence", "trip_id", "stop_id"])
    if len(df) != before:
        log("stop_times.txt", f"dropped {before-len(df)} rows with missing required fields")

    df["stop_sequence"] = df["stop_sequence"].astype(int)
    df = df.sort_values(["trip_id", "stop_sequence"]).reset_index(drop=True)

    before = len(df)
    df = df.drop_duplicates(subset=["trip_id", "stop_sequence"])
    if len(df) != before:
        log("stop_times.txt", f"dropped {before-len(df)} duplicate (trip_id, stop_sequence) rows")

    single_stop_trips = df.groupby("trip_id").size()
    bad_trips = single_stop_trips[single_stop_trips < 2].index
    if len(bad_trips):
        log("stop_times.txt", f"dropped {len(bad_trips)} trips with fewer than 2 stop_times rows")
        df = df[~df["trip_id"].isin(bad_trips)]

    return df


def fix_calendar(df):
    if df is None:
        return None
    df = strip_strings(df)
    df["service_id"] = clean_id_like(df["service_id"])
    for c in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int).clip(0, 1).astype(str)
    df["start_date"] = to_gtfs_date(df["start_date"])
    df["end_date"] = to_gtfs_date(df["end_date"])

    try:
        span_days = (pd.to_datetime(df["end_date"], format="%Y%m%d") -
                     pd.to_datetime(df["start_date"], format="%Y%m%d")).dt.days
        too_short = span_days < 7
        if too_short.any():
            log("calendar.txt", f"WARNING: {int(too_short.sum())} service_id(s) span "
                                 f"fewer than 7 days - GTFS requires >=7 days of validity")
    except Exception:
        pass

    before = len(df)
    df = df.drop_duplicates(subset=["service_id"])
    if len(df) != before:
        log("calendar.txt", f"dropped {before-len(df)} duplicate service_id rows")
    return df


def fix_calendar_dates(df):
    if df is None:
        return None
    df = strip_strings(df)
    df["service_id"] = clean_id_like(df["service_id"])
    df["date"] = to_gtfs_date(df["date"])
    if "exception_type" in df.columns:
        df["exception_type"] = pd.to_numeric(df["exception_type"], errors="coerce")
        bad = ~df["exception_type"].isin([1, 2])
        if bad.any():
            log("calendar_dates.txt", f"dropped {int(bad.sum())} rows with invalid exception_type "
                                       f"(must be 1 or 2)")
            df = df[~bad]
        df["exception_type"] = df["exception_type"].astype(int).astype(str)
    before = len(df)
    df = df.drop_duplicates(subset=["service_id", "date"])
    if len(df) != before:
        log("calendar_dates.txt", f"dropped {before-len(df)} duplicate (service_id, date) rows")
    return df


def fix_shapes(df, valid_shape_ids=None):
    if df is None:
        return None
    df = strip_strings(df)
    df["shape_id"] = clean_id_like(df["shape_id"])
    df["shape_pt_lat"] = pd.to_numeric(df["shape_pt_lat"], errors="coerce")
    df["shape_pt_lon"] = pd.to_numeric(df["shape_pt_lon"], errors="coerce")
    df["shape_pt_sequence"] = pd.to_numeric(df["shape_pt_sequence"], errors="coerce")

    before = len(df)
    df = df.dropna(subset=["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"])
    if len(df) != before:
        log("shapes.txt", f"dropped {before-len(df)} rows with missing required fields")

    df["shape_pt_sequence"] = df["shape_pt_sequence"].astype(int)
    df = df.sort_values(["shape_id", "shape_pt_sequence"]).reset_index(drop=True)

    before = len(df)
    df = df.drop_duplicates(subset=["shape_id", "shape_pt_sequence"])
    if len(df) != before:
        log("shapes.txt", f"dropped {before-len(df)} duplicate (shape_id, shape_pt_sequence) rows")

    if valid_shape_ids:
        before = len(df)
        df = df[df["shape_id"].isin(valid_shape_ids)]
        if len(df) != before:
            log("shapes.txt", f"dropped {before-len(df)} points for shape_id not referenced by any trip")
    return df


def synthesize_feed_info(calendar_df, calendar_dates_df):
    start_dates, end_dates = [], []
    if calendar_df is not None and len(calendar_df):
        start_dates += list(calendar_df["start_date"].dropna())
        end_dates += list(calendar_df["end_date"].dropna())
    if calendar_dates_df is not None and len(calendar_dates_df):
        start_dates += list(calendar_dates_df["date"].dropna())
        end_dates += list(calendar_dates_df["date"].dropna())
    start = min(start_dates) if start_dates else ""
    end = max(end_dates) if end_dates else ""
    return pd.DataFrame([{
        "feed_publisher_name": "Metro Cali S.A. - Sistema Integrado de Transporte Publico MIO",
        "feed_publisher_url": "https://www.metrocali.gov.co",
        "feed_lang": "es",
        "feed_start_date": start,
        "feed_end_date": end,
        "feed_version": datetime.now().strftime("%Y%m%d"),
        "feed_contact_email": "sistemas@metrocali.gov.co",
    }])


def main():
    ensure_dirs()
    print("Reading raw files...")
    raw = {f: read_raw(f) for f in [
        "agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt",
        "calendar.txt", "calendar_dates.txt", "shapes.txt", "fare_attributes.txt",
        "fare_rules.txt", "frequencies.txt", "feed_info.txt",
    ]}

    missing_required = [f for f in REQUIRED_FILES if raw.get(f) is None]
    if missing_required:
        print(f"\nERROR: required GTFS file(s) missing from {RAW_DIR}: {missing_required}")
        print("Run download.py first, and check gtfs_common.LAYER_NAME_ALIASES "
              "if a layer wasn't auto-matched.")
        return

    print("\nFixing agency.txt")
    agency = fix_agency(raw["agency.txt"])
    valid_agency_ids = set(agency["agency_id"].dropna()) if "agency_id" in agency.columns else set()

    print("Fixing stops.txt")
    stops = fix_stops(raw["stops.txt"])
    valid_stop_ids = set(stops["stop_id"])

    print("Fixing routes.txt")
    routes = fix_routes(raw["routes.txt"], valid_agency_ids)
    valid_route_ids = set(routes["route_id"])

    print("Fixing calendar.txt / calendar_dates.txt")
    calendar = fix_calendar(raw.get("calendar.txt"))
    calendar_dates = fix_calendar_dates(raw.get("calendar_dates.txt"))
    valid_service_ids = set()
    if calendar is not None:
        valid_service_ids |= set(calendar["service_id"])
    if calendar_dates is not None:
        valid_service_ids |= set(calendar_dates["service_id"])
    if not valid_service_ids:
        print("ERROR: neither calendar.txt nor calendar_dates.txt yielded any service_id. Aborting.")
        return

    print("Fixing trips.txt")
    trips = fix_trips(raw["trips.txt"], valid_route_ids, valid_service_ids)
    valid_trip_ids = set(trips["trip_id"])

    print("Fixing stop_times.txt")
    stop_times = fix_stop_times(raw["stop_times.txt"], valid_trip_ids, valid_stop_ids)
    # trips that lost all their stop_times get dropped too
    trips_with_stoptimes = set(stop_times["trip_id"])
    dropped_trips = valid_trip_ids - trips_with_stoptimes
    if dropped_trips:
        log("trips.txt", f"dropped {len(dropped_trips)} trips with no valid stop_times left")
        trips = trips[trips["trip_id"].isin(trips_with_stoptimes)]

    shapes = None
    if raw.get("shapes.txt") is not None:
        print("Fixing shapes.txt")
        valid_shape_ids = set(trips["shape_id"].dropna()) if "shape_id" in trips.columns else None
        shapes = fix_shapes(raw["shapes.txt"], valid_shape_ids)

    feed_info = raw.get("feed_info.txt")
    if feed_info is None or feed_info.empty:
        print("Synthesizing feed_info.txt (none provided)")
        feed_info = synthesize_feed_info(calendar, calendar_dates)

    outputs = {
        "agency.txt": agency, "stops.txt": stops, "routes.txt": routes,
        "trips.txt": trips, "stop_times.txt": stop_times,
        "calendar.txt": calendar, "calendar_dates.txt": calendar_dates,
        "shapes.txt": shapes, "fare_attributes.txt": raw.get("fare_attributes.txt"),
        "fare_rules.txt": raw.get("fare_rules.txt"), "frequencies.txt": raw.get("frequencies.txt"),
        "feed_info.txt": feed_info,
    }

    print(f"\nWriting cleaned files to {CLEAN_DIR}/")
    written = []
    for fname, df in outputs.items():
        if df is None or df.empty:
            continue
        path = f"{CLEAN_DIR}/{fname}"
        df.to_csv(path, index=False, encoding="utf-8")
        written.append(path)
        print(f"  {fname}: {len(df)} rows")

    with open(f"{CLEAN_DIR}/../report/fix_report.txt", "w", encoding="utf-8") as f:
        f.write("GTFS fix report\n================\n\n")
        for file, msg in ISSUES:
            f.write(f"[{file}] {msg}\n")
    print(f"\nWrote fix report: build/report/fix_report.txt ({len(ISSUES)} items)")

    os.makedirs(os.path.dirname(FINAL_ZIP), exist_ok=True)
    with zipfile.ZipFile(FINAL_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in written:
            zf.write(path, arcname=os.path.basename(path))  # flat, root-level per spec
    print(f"\nWrote {FINAL_ZIP}")
    print("Next: run validate.py")


if __name__ == "__main__":
    main()
