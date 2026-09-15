"""
CLI wrapper for diagnose_service_id_daytypes.py.

Fetches "rutas" live (for the HABIL/SABADO/DOM_FEST windows), reads
routes.txt / trips.txt / stop_times.txt from build/gtfs_raw/ (after
download.py AND enrich_routes.py have run - the route_id -> real
letter-code mapping this relies on comes from route_short_name, which
enrich_routes.py corrects), and prints the day-type fit diagnostic.

Usage:
    python download.py
    python enrich_routes.py
    python run_diagnose_service_id_daytypes.py
"""
import argparse

import pandas as pd

import arcgis_client as AC
import diagnose_service_id_daytypes as DST
from gtfs_common import (
    RAW_DIR, REPORT_DIR, ROUTES_INFO_FEATURE_SERVICE_URL, ROUTES_INFO_ITEM_ID,
    ROUTES_INFO_LAYER_ID, ensure_dirs,
)


def main(routes_path, trips_path, stop_times_path):
    ensure_dirs()
    try:
        routes_df = pd.read_csv(routes_path, dtype=str, keep_default_na=False, na_values=[""])
        trips_df = pd.read_csv(trips_path, dtype=str, keep_default_na=False, na_values=[""])
        stop_times_df = pd.read_csv(stop_times_path, dtype=str, keep_default_na=False, na_values=[""])
    except FileNotFoundError as e:
        print(f"Could not find required local file ({e}). Run download.py and "
              f"enrich_routes.py first.")
        return

    if "route_short_name" not in routes_df.columns:
        print("routes.txt has no route_short_name column - nothing to map routes to "
              "real letter codes with. Run enrich_routes.py first.")
        return
    route_id_to_base_code = dict(zip(routes_df["route_id"], routes_df["route_short_name"]))

    print(f"Resolving rutas item {ROUTES_INFO_ITEM_ID}...")
    try:
        service_url = AC.resolve_feature_service_url(
            ROUTES_INFO_ITEM_ID, fallback_url=ROUTES_INFO_FEATURE_SERVICE_URL
        )
    except Exception as e:
        print(f"Could not resolve rutas Feature Service ({e}). Cannot run this diagnostic "
              f"without it.")
        return

    print(f"Fetching layer {ROUTES_INFO_LAYER_ID}...")
    ext_df = AC.query_all_records(service_url, ROUTES_INFO_LAYER_ID, "rutas")
    if ext_df.empty:
        print("rutas returned 0 rows. Cannot run this diagnostic.")
        return

    print("\nExtracting published HABIL/SABADO/DOM_FEST windows per route...")
    route_daytype_windows = DST.extract_route_daytype_windows(ext_df)

    print("\nComputing real trip-time spans per (route_id, service_id)...")
    spans = DST.compute_trip_spans(trips_df, stop_times_df)
    print(f"  {len(spans)} (route_id, service_id) group(s) found")

    print("\nComparing spans against published windows...")
    analysis = DST.analyze(route_daytype_windows, spans, route_id_to_base_code)
    aggregate = DST.aggregate_by_service_id(analysis)

    print()
    print(DST.format_report(aggregate, analysis))

    analysis_out = analysis.copy()
    if "all_scores" not in analysis_out.columns:
        analysis_out["all_scores"] = ""
    analysis_out.to_csv(f"{REPORT_DIR}/service_id_daytype_analysis.csv", index=False)
    aggregate_out = aggregate.copy()
    if not aggregate_out.empty:
        aggregate_out["votes"] = aggregate_out["votes"].astype(str)
    aggregate_out.to_csv(f"{REPORT_DIR}/service_id_daytype_summary.csv", index=False)
    print(f"\nWrote {REPORT_DIR}/service_id_daytype_analysis.csv (per-route evidence)")
    print(f"Wrote {REPORT_DIR}/service_id_daytype_summary.csv (per-service_id aggregate)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routes", default=f"{RAW_DIR}/routes.txt")
    parser.add_argument("--trips", default=f"{RAW_DIR}/trips.txt")
    parser.add_argument("--stop-times", default=f"{RAW_DIR}/stop_times.txt")
    args = parser.parse_args()
    main(args.routes, args.trips, args.stop_times)
