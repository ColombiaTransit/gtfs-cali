"""
validate_routes_geometry.py
=============================
Cross-validates the FINAL, cleaned shapes.txt (after fix.py has run)
against the real route geometry in Metro Cali's separate "rutas" dataset.
This is a geometric ground-truth check - not spec validation - answering
"does our reconstructed path actually match the official route?" the same
way ptosparadas gave us a ground-truth check for stop identity, except
here via polyline proximity instead of point-distance matching, since
routes are lines rather than points.

Run this AFTER fix.py has produced build/gtfs_clean/{shapes,trips,routes}.txt:
    python download.py
    python enrich_stops.py
    python enrich_routes.py
    python fix.py
    python validate_routes_geometry.py    # <- this script
    python validate.py

Non-fatal / informational: this never blocks the pipeline. It writes a
per-shape report to build/report/shape_geometry_validation.csv and prints
a summary so you know which reconstructed routes are worth a manual look.
"""
import pandas as pd

import arcgis_client as AC
import routes_enrich
import shape_geometry_validate as SGV
from gtfs_common import (
    CLEAN_DIR, REPORT_DIR, ROUTES_INFO_FEATURE_SERVICE_URL, ROUTES_INFO_ITEM_ID,
    ROUTES_INFO_LAYER_ID, ensure_dirs,
)


def load_our_shapes():
    shapes = pd.read_csv(f"{CLEAN_DIR}/shapes.txt", dtype=str, keep_default_na=False, na_values=[""])
    shapes["shape_pt_lat"] = pd.to_numeric(shapes["shape_pt_lat"], errors="coerce")
    shapes["shape_pt_lon"] = pd.to_numeric(shapes["shape_pt_lon"], errors="coerce")
    shapes["shape_pt_sequence"] = pd.to_numeric(shapes["shape_pt_sequence"], errors="coerce")
    shapes = shapes.sort_values(["shape_id", "shape_pt_sequence"])

    groups = {}
    for shape_id, g in shapes.groupby("shape_id"):
        groups[shape_id] = list(zip(g["shape_pt_lat"], g["shape_pt_lon"]))
    return groups


def load_shape_to_route_map():
    trips = pd.read_csv(f"{CLEAN_DIR}/trips.txt", dtype=str, keep_default_na=False, na_values=[""])
    if "shape_id" not in trips.columns:
        return {}
    trips = trips.dropna(subset=["shape_id"])
    # one route_id per shape_id (first trip using that shape wins; in
    # practice a shape_id should only ever belong to one route)
    return dict(zip(trips["shape_id"], trips["route_id"]))


def load_raw_shapes_and_trips():
    """Un-grouped versions of shapes.txt/trips.txt (plain DataFrames), needed
    to build one representative shape per route_id for the geometry-based
    route-identification pass (see routes_enrich.build_route_representative_shapes)."""
    shapes = pd.read_csv(f"{CLEAN_DIR}/shapes.txt", dtype=str, keep_default_na=False, na_values=[""])
    shapes["shape_pt_lat"] = pd.to_numeric(shapes["shape_pt_lat"], errors="coerce")
    shapes["shape_pt_lon"] = pd.to_numeric(shapes["shape_pt_lon"], errors="coerce")
    shapes["shape_pt_sequence"] = pd.to_numeric(shapes["shape_pt_sequence"], errors="coerce")
    trips = pd.read_csv(f"{CLEAN_DIR}/trips.txt", dtype=str, keep_default_na=False, na_values=[""])
    return shapes, trips


def main():
    ensure_dirs()
    print("Loading our reconstructed shapes.txt / trips.txt (post-fix.py)...")
    try:
        shape_groups = load_our_shapes()
        shape_id_to_route_id = load_shape_to_route_map()
        raw_shapes_df, raw_trips_df = load_raw_shapes_and_trips()
    except FileNotFoundError as e:
        print(f"Could not find cleaned GTFS files ({e}). Run download.py -> "
              f"enrich_stops.py -> enrich_routes.py -> fix.py first.")
        return
    print(f"  {len(shape_groups)} shape(s) loaded")

    print(f"\nResolving rutas item {ROUTES_INFO_ITEM_ID}...")
    try:
        service_url = AC.resolve_feature_service_url(
            ROUTES_INFO_ITEM_ID, fallback_url=ROUTES_INFO_FEATURE_SERVICE_URL
        )
    except Exception as e:
        print(f"Could not resolve rutas Feature Service ({e}). Skipping geometry validation.")
        return

    print(f"Fetching layer {ROUTES_INFO_LAYER_ID} (with geometry this time)...")
    ext_df = AC.query_all_records(service_url, ROUTES_INFO_LAYER_ID, "rutas")
    if ext_df.empty or "_geom_path" not in ext_df.columns:
        print("rutas returned no usable geometry. Skipping geometry validation.")
        return

    # IMPORTANT: our route_id is often NOT the same ID space as rutas' RUTA
    # (confirmed on a live run - our route_id is a plain internal integer,
    # e.g. "112", with no relation to the real letter-coded route number).
    # ID-based base-code grouping alone would leave every shape unmatched
    # (NO_CANDIDATE), so first identify each route's real counterpart by
    # geometry (same approach as routes_enrich.enrich_routes' fallback),
    # then use THAT route's full variant group (all its directions) as the
    # candidate set for the actual per-shape distance comparison below.
    print("Identifying each route's real rutas counterpart by geometry "
          "(our route_id often isn't in the same ID space as RUTA)...")
    route_shapes = routes_enrich.build_route_representative_shapes(raw_shapes_df, raw_trips_df)
    route_geo_matches = routes_enrich.match_routes_by_geometry(route_shapes, ext_df)
    base_groups = routes_enrich.build_route_variant_map(ext_df)

    route_variant_geometry = {}
    for route_id, info in route_geo_matches.items():
        if info["mean_m"] > routes_enrich.ROUTE_MATCH_TRUST_THRESHOLD_M:
            continue
        base = routes_enrich.base_route_code(info["ruta"])
        variants = base_groups.get(base, [(info["ruta"], info["ext_idx"])])
        route_variant_geometry[route_id] = {
            ruta: ext_df.loc[idx, "_geom_path"] for ruta, idx in variants
            if ext_df.loc[idx, "_geom_path"]
        }
    print(f"  {len(route_variant_geometry)}/{len(route_shapes)} route(s) identified "
          f"with a trusted rutas counterpart")

    print("\nComparing shape geometry (nearest-vertex distance, meters)...")
    report = SGV.validate_shapes(shape_groups, shape_id_to_route_id, route_variant_geometry)
    report.to_csv(f"{REPORT_DIR}/shape_geometry_validation.csv", index=False)

    print(f"\n=== Shape geometry validation summary ===")
    if len(report):
        print(report["status"].value_counts().to_string())
        print(f"\nWorst offenders (highest mean distance):")
        worst = report.sort_values("mean_m", ascending=False, na_position="last").head(5)
        for _, row in worst.iterrows():
            if pd.isna(row["mean_m"]):
                print(f"  shape_id={row['shape_id']:<12} route_id={row['route_id']} -> "
                      f"no rutas candidate found for this route_id")
            else:
                print(f"  shape_id={row['shape_id']:<12} route_id={row['route_id']} -> "
                      f"matched '{row['matched_ruta']}', mean={row['mean_m']:.1f}m, "
                      f"status={row['status']}")
    else:
        print("No shapes to compare.")
    print(f"\nFull report: {REPORT_DIR}/shape_geometry_validation.csv")
    print(f"Thresholds: OK <= {SGV.OK_THRESHOLD_M}m, WARN <= {SGV.WARN_THRESHOLD_M}m, else FAIL")


if __name__ == "__main__":
    main()
