"""
Step 1 - Download
==================
Fetches the 9 layers/tables from the Metro Cali FeatureServer (a relational
vehicle-schedule model, not flat GTFS tables - see reconstruct.py for the
full explanation of the schema and join chain), reconstructs standard GTFS
tables via reconstruct.py, and writes raw GTFS .txt files.

Run this on a machine with normal internet access (it needs to reach
arcgis.com / services9.arcgis.com), e.g.:

    python download.py

Requires: requests, pandas  (see requirements.txt)

IMPORTANT: two things about this source schema can't be known for certain
without inspecting live data, and are auto-detected at runtime (printed as
"[diagnostic]" lines):
  1. Whether ScheduleElements rows are one-per-stop or one-per-segment
     (see reconstruct.detect_schedule_alignment)
  2. Whether StartRun/Arrival/Departure are in hours, minutes, or seconds
     (see reconstruct.detect_time_unit)
Read the diagnostic output on first run. If detection raises an error (too
ambiguous to auto-pick), it tells you exactly what to hard-set in
gtfs_common.py (TIME_UNIT_OVERRIDE / SCHEDULE_ALIGNMENT_OVERRIDE).

Also sanity-check a handful of the printed sample trips against any
schedule info you can find published by Metro Cali (app, website, PDF
timetables) before trusting this feed for anything user-facing.
"""
import json
import sys
import time

import pandas as pd
import requests

import reconstruct as R
from gtfs_common import (
    ARCGIS_ITEM_URL, FEATURE_SERVICE_URL, KNOWN_AGENCY, LAYER_IDS, RAW_DIR,
    ensure_dirs,
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "metrocali-gtfs-pipeline/1.0"})
PAGE_SIZE = 2000
TIMEOUT = 60


def get_json(url, **params):
    params.setdefault("f", "json")
    for attempt in range(3):
        try:
            r = SESSION.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(f"ArcGIS API error for {url}: {data['error']}")
            return data
        except (requests.RequestException, json.JSONDecodeError) as e:
            if attempt == 2:
                raise
            print(f"  retry ({attempt+1}/3) after error: {e}")
            time.sleep(2)


def resolve_feature_service_url():
    """Try the ArcGIS item API first (handles Metro Cali repointing the item
    to a new service in a future 'vigencia'); fall back to the confirmed
    live URL in gtfs_common if that fails for any reason."""
    try:
        item = get_json(ARCGIS_ITEM_URL)
        url = item.get("url")
        if url:
            print(f"Resolved Feature Service from item API: {url}")
            return url
    except Exception as e:
        print(f"  item-API resolution failed ({e}); falling back to known URL")
    print(f"Using known Feature Service URL: {FEATURE_SERVICE_URL}")
    return FEATURE_SERVICE_URL


def query_all_records(service_url, layer_id, label):
    """Paginate through a layer/table. Returns a DataFrame; point
    geometries add stop_lat/stop_lon; polyline geometries add _geom_path
    (list of (lat, lon) tuples in the order ArcGIS returns them)."""
    query_url = f"{service_url}/{layer_id}/query"
    offset = 0
    rows = []
    while True:
        params = {
            "where": "1=1", "outFields": "*", "f": "json",
            "resultOffset": offset, "resultRecordCount": PAGE_SIZE,
            "outSR": 4326, "returnGeometry": "true",
        }
        data = get_json(query_url, **params)
        feats = data.get("features", [])
        if not feats:
            break
        for feat in feats:
            attrs = dict(feat.get("attributes", {}))
            geom = feat.get("geometry")
            if geom:
                if "x" in geom and "y" in geom:
                    attrs["stop_lat"] = geom["y"]
                    attrs["stop_lon"] = geom["x"]
                elif "paths" in geom and geom["paths"]:
                    # a feature can have multiple parts; concatenate them in order
                    path = [pt for part in geom["paths"] for pt in part]
                    attrs["_geom_path"] = [(lat, lon) for lon, lat in path]
            rows.append(attrs)
        got = len(feats)
        offset += got
        print(f"    [{label}] fetched {offset} records so far...")
        if not data.get("exceededTransferLimit") and got < PAGE_SIZE:
            break
    return pd.DataFrame(rows)


def fetch_all_layers(service_url):
    layers = {}
    for name, layer_id in LAYER_IDS.items():
        print(f"Fetching layer {layer_id}: {name}")
        layers[name] = query_all_records(service_url, layer_id, name)
        print(f"  -> {len(layers[name])} rows")
    return layers


def build_agency_df():
    return pd.DataFrame([KNOWN_AGENCY])


def main():
    ensure_dirs()
    service_url = resolve_feature_service_url()
    print()

    layers = fetch_all_layers(service_url)
    print("\n=== Reconstructing GTFS tables ===")

    stops_internal = R.build_stops(layers["Stops"])
    by_id, by_oid = R.stop_id_lookup(stops_internal)
    stops_df = stops_internal.drop(columns=["_internal_id", "_internal_objectid"])

    routes_internal = R.build_routes(layers["Lines"])
    lines_id_to_route_id = dict(zip(routes_internal["_internal_id"], routes_internal["route_id"]))
    routes_df = routes_internal.drop(columns=["_internal_id"])

    calendar_internal = R.build_calendar(layers["Calendars"])
    calendar_id_to_service_id = R.calendar_id_lookup(calendar_internal)
    calendar_df = calendar_internal.drop(columns=["_internal_id"])
    calendar_dates_df = R.build_calendar_dates(layers["CalendarExceptions"])

    print("Building stop sequences per pattern (LineVariants x LineVariantElements)...")
    stop_sequences, geometries = R.build_stop_sequence_per_pattern(
        layers["LineVariantElements"], by_id, by_oid
    )

    line_variant_lookup = R.build_line_variant_lookup(layers["LineVariants"], lines_id_to_route_id)

    print("Building shapes.txt from real pattern geometry...")
    shapes_df = R.build_shapes(line_variant_lookup, geometries)

    print("\nAuto-detecting schedule alignment and time unit (see diagnostics)...")
    alignment = R.detect_schedule_alignment(layers["ScheduleElements"], layers["Schedules"], stop_sequences)
    time_unit = R.detect_time_unit(layers["Runs"], layers["ScheduleElements"], layers["Schedules"])

    print("\nBuilding trips.txt and stop_times.txt...")
    trips_df, stop_times_df = R.build_trips_and_stop_times(
        layers["Runs"], layers["Schedules"], layers["ScheduleElements"],
        line_variant_lookup, stop_sequences, calendar_id_to_service_id,
        time_unit, alignment,
    )

    agency_df = build_agency_df()

    outputs = {
        "agency.txt": agency_df, "stops.txt": stops_df, "routes.txt": routes_df,
        "trips.txt": trips_df, "stop_times.txt": stop_times_df,
        "calendar.txt": calendar_df, "calendar_dates.txt": calendar_dates_df,
        "shapes.txt": shapes_df,
    }

    print(f"\nWriting raw files to {RAW_DIR}/")
    for fname, df in outputs.items():
        if df is None or df.empty:
            print(f"  {fname}: SKIPPED (empty)")
            continue
        df.to_csv(f"{RAW_DIR}/{fname}", index=False, encoding="utf-8")
        print(f"  {fname}: {len(df)} rows")

    print("\n=== Sample reconstructed trips (sanity-check these!) ===")
    for trip_id in (trips_df["trip_id"].head(3) if len(trips_df) else []):
        route = trips_df.loc[trips_df["trip_id"] == trip_id, "route_id"].iloc[0]
        st = stop_times_df[stop_times_df["trip_id"] == trip_id].sort_values("stop_sequence")
        print(f"  trip_id={trip_id} route_id={route}")
        for _, row in st.iterrows():
            print(f"    seq={row['stop_sequence']:>2}  stop={row['stop_id']:<10} "
                  f"arr={row['arrival_time']}  dep={row['departure_time']}")

    print("\nKNOWN DATA GAPS (source has no values for these - placeholders "
          "get added in fix.py, but you should get real values from Metro "
          "Cali / sistemas@metrocali.gov.co before publishing):")
    print("  - stops.txt: stop_name is empty for all stops")
    print("  - routes.txt: route_short_name / route_long_name are empty for all routes")
    print("\nNext: run fix.py")


if __name__ == "__main__":
    main()
