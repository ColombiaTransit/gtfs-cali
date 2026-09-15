"""
Step 1.5 - Enrich stop names (CLI)
====================================
Thin network/CLI wrapper: fetches the "ptosparadas" dataset (Paradas en
Corredores Troncales, Pretroncales y Alimentadores del MIO) via
arcgis_client, then hands off to stops_enrich.enrich_stops() - the pure,
independently-tested matching logic (see stops_enrich.py / test_stops_enrich.py)
- to patch stop_name into the raw stops.txt written by download.py.

Confirmed live endpoint (gtfs_common.STOPS_INFO_FEATURE_SERVICE_URL):
    https://services9.arcgis.com/8rJ42n9yWry0I4K4/arcgis/rest/services/ptosparadas/FeatureServer/0
Confirmed fields: FID, STOPID, DIRECCION, COMPLEMENT, DESCRIP, T_PARADA,
CORREDOR, BARRIO, SECTOR, ZONA, FOTO_1, FOTO_2, FUENTE, LATITUD, LONGITUD.
STOPID is the real join key (matches our stop_id with 100% coverage in
testing against the live schema shape). DIRECCION (cross-streets, e.g.
"Av 15 Oe entre Cl 7 Oe y 8 Oe") is populated on every row and is what
becomes stop_name; COMPLEMENT (a landmark, when present) gets appended in
parentheses. DESCRIP is almost always blank and is correctly ignored.

Run this AFTER download.py and BEFORE fix.py:
    python download.py
    python enrich_stops.py
    python fix.py

Best-effort and non-fatal: if the ptosparadas item can't be resolved, or
nothing matches, stops.txt is left untouched and fix.py's placeholder
fallback ("Parada <stop_id>") still applies.
"""
import pandas as pd

import arcgis_client as AC
import stops_enrich
from gtfs_common import (
    RAW_DIR, REPORT_DIR, STOPS_INFO_FEATURE_SERVICE_URL, STOPS_INFO_ITEM_ID,
    STOPS_INFO_LAYER_ID, ensure_dirs,
)


def main():
    ensure_dirs()
    print(f"Resolving ptosparadas item {STOPS_INFO_ITEM_ID}...")
    try:
        service_url = AC.resolve_feature_service_url(
            STOPS_INFO_ITEM_ID, fallback_url=STOPS_INFO_FEATURE_SERVICE_URL
        )
    except Exception as e:
        print(f"Could not resolve ptosparadas Feature Service ({e}). "
              f"Skipping enrichment - stops.txt is left as-is; fix.py's "
              f"placeholder names will apply instead.")
        return

    print(f"Fetching layer {STOPS_INFO_LAYER_ID}...")
    ext_df = AC.query_all_records(service_url, STOPS_INFO_LAYER_ID, "ptosparadas")
    if ext_df.empty:
        print("ptosparadas returned 0 rows. Skipping enrichment.")
        return
    if "_geom_lat" in ext_df.columns:
        ext_df = ext_df.rename(columns={"_geom_lat": "ext_lat", "_geom_lon": "ext_lon"})

    stops_path = f"{RAW_DIR}/stops.txt"
    our_stops = pd.read_csv(stops_path, dtype=str, keep_default_na=False, na_values=[""])
    our_stops["stop_lat"] = pd.to_numeric(our_stops["stop_lat"], errors="coerce")
    our_stops["stop_lon"] = pd.to_numeric(our_stops["stop_lon"], errors="coerce")

    if "stop_name" in our_stops.columns:
        n_before = int((our_stops["stop_name"].notna() & (our_stops["stop_name"].str.strip() != "")).sum())
    else:
        n_before = 0

    enriched, stats = stops_enrich.enrich_stops(our_stops, ext_df)

    n_after = int(enriched["stop_name"].notna().sum()) if "stop_name" in enriched.columns else 0
    enriched.to_csv(stops_path, index=False, encoding="utf-8")

    audit = enriched[["stop_id", "stop_name"]].copy() if "stop_name" in enriched.columns else enriched[["stop_id"]].copy()
    audit["matched"] = audit.get("stop_name", pd.Series(dtype=str)).notna()
    audit.to_csv(f"{REPORT_DIR}/stop_enrichment.csv", index=False)

    print(f"\n=== Enrichment summary ===")
    print(f"stops.txt total rows: {len(enriched)}")
    print(f"stop_name populated before: {n_before}")
    print(f"stop_name populated after:  {n_after}  (+{n_after - n_before})")
    print(f"  via ID match:      {stats['id_matched']}")
    print(f"  via spatial match: {stats['spatial_matched']}")
    print(f"Audit trail: {REPORT_DIR}/stop_enrichment.csv")
    if n_after < len(enriched):
        print(f"{len(enriched) - n_after} stop(s) still have no name - "
              f"fix.py will placeholder these as 'Parada <stop_id>'.")
    print("\nNext: run fix.py")


if __name__ == "__main__":
    main()
