"""
Step 1.6 - Enrich route names (CLI)
======================================
Thin network/CLI wrapper: fetches the "rutas" dataset (Rutas del MIO) via
arcgis_client, then hands off to routes_enrich.enrich_routes() - the pure,
independently-tested matching logic (see routes_enrich.py /
test_routes_enrich.py) - to patch route_long_name into the raw routes.txt
written by download.py.

Confirmed live endpoint (gtfs_common.ROUTES_INFO_FEATURE_SERVICE_URL):
    https://services9.arcgis.com/8rJ42n9yWry0I4K4/arcgis/rest/services/rutas/FeatureServer/0
Confirmed fields: FID, RUTA, NOMBRE, DIA_TIPO, VARIANTE, DIA_VARI,
ID_SERVICI, SERVICIO, TIPOLOGIA, FECHA_IMPL, PSO, OBSERVACIO, HABIL,
SABADO, DOM_FEST, FRANJA, LONGITUD, FINALIZA, PSO_IMPL, Shape__Length, plus
real LineString/MultiLineString route geometry (not used for matching here).
RUTA (e.g. "A01A") is a base route code + an optional trailing direction/
variant letter; NOMBRE (e.g. "ESTACIÓN SAN BOSCO - CAM - CENTRO") is the
descriptive name that becomes route_long_name.

Run this AFTER download.py and BEFORE fix.py:
    python download.py
    python enrich_stops.py
    python enrich_routes.py
    python fix.py

Best-effort and non-fatal: if the rutas item can't be resolved, or match
coverage is too low to trust, routes.txt is left untouched and fix.py's
placeholder fallback (route_short_name = route_id) still applies.
"""
import pandas as pd

import arcgis_client as AC
import routes_enrich
from gtfs_common import (
    RAW_DIR, REPORT_DIR, ROUTES_INFO_FEATURE_SERVICE_URL, ROUTES_INFO_ITEM_ID,
    ROUTES_INFO_LAYER_ID, ensure_dirs,
)


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

    print(f"Fetching layer {ROUTES_INFO_LAYER_ID}...")
    ext_df = AC.query_all_records(service_url, ROUTES_INFO_LAYER_ID, "rutas")
    if ext_df.empty:
        print("rutas returned 0 rows. Skipping enrichment.")
        return
    # Geometry isn't used for route-name matching (routes are lines, not
    # points) - drop it so it doesn't blow up memory/CSV size unnecessarily.
    ext_df = ext_df.drop(columns=[c for c in ("_geom_path", "_geom_lat", "_geom_lon") if c in ext_df.columns])

    routes_path = f"{RAW_DIR}/routes.txt"
    our_routes = pd.read_csv(routes_path, dtype=str, keep_default_na=False, na_values=[""])

    n_before = 0
    if "route_long_name" in our_routes.columns:
        n_before = int((our_routes["route_long_name"].notna() &
                        (our_routes["route_long_name"].str.strip() != "")).sum())

    enriched, stats = routes_enrich.enrich_routes(our_routes, ext_df)

    n_after = 0
    if "route_long_name" in enriched.columns:
        n_after = int((enriched["route_long_name"].notna() &
                        (enriched["route_long_name"].astype(str).str.strip() != "")).sum())
    enriched.to_csv(routes_path, index=False, encoding="utf-8")

    audit_cols = ["route_id"] + (["route_long_name"] if "route_long_name" in enriched.columns else [])
    audit = enriched[audit_cols].copy()
    audit["matched"] = audit.get("route_long_name", pd.Series(dtype=str)).notna() & \
        (audit.get("route_long_name", pd.Series(dtype=str)).astype(str).str.strip() != "")
    audit.to_csv(f"{REPORT_DIR}/route_enrichment.csv", index=False)

    print(f"\n=== Enrichment summary ===")
    print(f"routes.txt total rows: {len(enriched)}")
    print(f"route_long_name populated before: {n_before}")
    print(f"route_long_name populated after:  {n_after}  (+{n_after - n_before})")
    print(f"Audit trail: {REPORT_DIR}/route_enrichment.csv")
    if n_after < len(enriched):
        print(f"{len(enriched) - n_after} route(s) still have no long name - "
              f"fix.py's route_short_name=route_id placeholder still applies to those.")
    print("\nNext: run fix.py")


if __name__ == "__main__":
    main()
