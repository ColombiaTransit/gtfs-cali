"""
Step 1.6 - Enrich route names (CLI)
======================================
Thin network/CLI wrapper: fetches the "rutas" dataset (Rutas del MIO) via
arcgis_client, then hands off to routes_enrich.enrich_routes() - the pure,
independently-tested matching logic (see routes_enrich.py /
test_routes_enrich.py) - to patch route_long_name (and, when matched by
geometry, a corrected route_short_name) into the raw routes.txt written by
download.py.

Confirmed live endpoint (gtfs_common.ROUTES_INFO_FEATURE_SERVICE_URL):
    https://services9.arcgis.com/8rJ42n9yWry0I4K4/arcgis/rest/services/rutas/FeatureServer/0
Confirmed fields: FID, RUTA, NOMBRE, DIA_TIPO, VARIANTE, DIA_VARI,
ID_SERVICI, SERVICIO, TIPOLOGIA, FECHA_IMPL, PSO, OBSERVACIO, HABIL,
SABADO, DOM_FEST, FRANJA, LONGITUD, FINALIZA, PSO_IMPL, Shape__Length, plus
real LineString/MultiLineString route geometry.

IMPORTANT (confirmed against a live run): our reconstructed route_id is a
PLAIN INTEGER internal scheduling-system ID from the GTFS FeatureServer's
Lines table (e.g. "112") - NOT the real, letter-coded MIO route number
("A01", "T14", "P52A", confirmed via metrocali.gov.co/newmioapp and public
Metro Cali communications). ID-based matching against RUTA therefore
reliably scores ~0% for this feed. routes_enrich.enrich_routes()
automatically falls back to matching by real route GEOMETRY (comparing our
reconstructed shapes.txt against every rutas line and taking the closest)
when this happens - this is why this script now also loads shapes.txt and
trips.txt, not just routes.txt.

Run this AFTER download.py and BEFORE fix.py:
    python download.py
    python enrich_stops.py
    python enrich_routes.py
    python fix.py

Best-effort and non-fatal: if the rutas item can't be resolved, or no
match clears the trust threshold, routes.txt is left untouched and fix.py's
placeholder fallback (route_short_name = route_id) still applies.
"""
import pandas as pd

import arcgis_client as AC
import routes_enrich
from gtfs_common import (
    RAW_DIR, REPORT_DIR, ROUTES_INFO_FEATURE_SERVICE_URL, ROUTES_INFO_ITEM_ID,
    ROUTES_INFO_LAYER_ID, ensure_dirs,
)


def load_shapes_and_trips():
    """Best-effort load of shapes.txt/trips.txt from the raw download - only
    needed for the geometry-matching fallback, so missing files aren't fatal
    (ID matching will just be all that's attempted)."""
    try:
        shapes_df = pd.read_csv(f"{RAW_DIR}/shapes.txt", dtype=str, keep_default_na=False, na_values=[""])
        shapes_df["shape_pt_lat"] = pd.to_numeric(shapes_df["shape_pt_lat"], errors="coerce")
        shapes_df["shape_pt_lon"] = pd.to_numeric(shapes_df["shape_pt_lon"], errors="coerce")
        shapes_df["shape_pt_sequence"] = pd.to_numeric(shapes_df["shape_pt_sequence"], errors="coerce")
        trips_df = pd.read_csv(f"{RAW_DIR}/trips.txt", dtype=str, keep_default_na=False, na_values=[""])
        return shapes_df, trips_df
    except FileNotFoundError as e:
        print(f"  (no shapes/trips data for geometry fallback: {e})")
        return None, None


def main():
    ensure_dirs()
    print(f"Resolving rutas item {ROUTES_INFO_ITEM_ID}...")
    try:
        service_url = AC.resolve_feature_service_url(
            ROUTES_INFO_ITEM_ID, fallback_url=ROUTES_INFO_FEATURE_SERVICE_URL
        )
    except Exception as e:
        print(f"Could not resolve rutas Feature Service ({e}). "
              f"Skipping enrichment - routes.txt is left as-is; fix.py's "
              f"placeholder route_short_name will apply instead.")
        return

    print(f"Fetching layer {ROUTES_INFO_LAYER_ID} (with geometry, for the matching fallback)...")
    ext_df = AC.query_all_records(service_url, ROUTES_INFO_LAYER_ID, "rutas")
    if ext_df.empty:
        print("rutas returned 0 rows. Skipping enrichment.")
        return

    routes_path = f"{RAW_DIR}/routes.txt"
    our_routes = pd.read_csv(routes_path, dtype=str, keep_default_na=False, na_values=[""])
    shapes_df, trips_df = load_shapes_and_trips()

    n_before = 0
    if "route_long_name" in our_routes.columns:
        n_before = int((our_routes["route_long_name"].notna() &
                        (our_routes["route_long_name"].str.strip() != "")).sum())
    short_before = dict(zip(our_routes["route_id"], our_routes.get("route_short_name", "")))

    enriched, stats = routes_enrich.enrich_routes(our_routes, ext_df, shapes_df=shapes_df, trips_df=trips_df)

    n_after = 0
    if "route_long_name" in enriched.columns:
        n_after = int((enriched["route_long_name"].notna() &
                        (enriched["route_long_name"].astype(str).str.strip() != "")).sum())
    n_short_code_fixed = int(sum(
        1 for _, row in enriched.iterrows()
        if str(short_before.get(row["route_id"], "")) != str(row.get("route_short_name", ""))
    ))
    enriched.to_csv(routes_path, index=False, encoding="utf-8")

    audit_cols = ["route_id", "route_short_name"] + (["route_long_name"] if "route_long_name" in enriched.columns else [])
    audit = enriched[audit_cols].copy()
    audit["matched"] = audit.get("route_long_name", pd.Series(dtype=str)).notna() & \
        (audit.get("route_long_name", pd.Series(dtype=str)).astype(str).str.strip() != "")
    audit.to_csv(f"{REPORT_DIR}/route_enrichment.csv", index=False)

    print(f"\n=== Enrichment summary ===")
    print(f"routes.txt total rows: {len(enriched)}")
    print(f"match method used: {stats['method'] or 'none'}")
    print(f"route_long_name populated before: {n_before}")
    print(f"route_long_name populated after:  {n_after}  (+{n_after - n_before})")
    if stats["method"] == "geometry":
        print(f"route_short_name corrected to real letter code: {n_short_code_fixed}")
    print(f"Audit trail: {REPORT_DIR}/route_enrichment.csv")
    if n_after < len(enriched):
        print(f"{len(enriched) - n_after} route(s) still have no long name - "
              f"fix.py's route_short_name=route_id placeholder still applies to those.")
    print("\nNext: run fix.py")


if __name__ == "__main__":
    main()
