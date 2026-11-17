"""
Step 1.6 - Route codes and names (CLI)
======================================
Sets route_short_name and route_long_name in the raw routes.txt written by download.py.

* route_short_name = the real MIO route code, DECODED from the internal route_id
  (route_codes.py: 2211 -> P21A, 302 -> A02 ...). No network and no guessing needed.
* route_long_name  = the variant description from Metro Cali's listadoRutas.php
  (live, with a dated snapshot as fallback), e.g. "ESTACIÓN SAN BOSCO - CAM - CENTRO".
  Routes that are not in listadoRutas (A01B, A73) keep the decoded code only.

This replaces the earlier matching by shape geometry, which was ambiguous and sometimes
wrong (e.g. T12 matched as P21). The geometry code remains in routes_enrich.py and is
still used by validate_routes_geometry.py.

Run AFTER download.py and BEFORE fix.py:
    python download.py && python enrich_stops.py && python enrich_routes.py && python fix.py
"""
from collections import Counter

import pandas as pd

import schedule_reference as SR
from gtfs_common import RAW_DIR, REPORT_DIR, ensure_dirs
from routes_from_codes import apply_route_codes


def main():
    ensure_dirs()
    routes_path = f"{RAW_DIR}/routes.txt"
    routes = pd.read_csv(routes_path, dtype=str, keep_default_na=False, na_values=[""])

    rows, label = SR.load_listado_rows()
    if not rows:
        print("No listadoRutas data (live or snapshot): long names stay empty, codes are still decoded.")
    enriched, audit = apply_route_codes(routes, rows)
    enriched.to_csv(routes_path, index=False, encoding="utf-8")
    audit.to_csv(f"{REPORT_DIR}/route_enrichment.csv", index=False)

    n = len(enriched)
    print("\n=== Route codes and names ===")
    print(f"routes.txt rows: {n}; listadoRutas source: {label or 'none'}")
    print(f"route codes: {dict(Counter(audit['code_source']))}")
    print(f"long name set: {int((audit['route_long_name'] != '').sum())}/{n}")
    not_in_listado = audit.loc[(audit['code_source'] == 'decoded') & (~audit['in_listado']), 'route_short_name'].tolist()
    if not_in_listado:
        print(f"decoded but not in listadoRutas (code only, no long name): {not_in_listado}")
    bad = audit.loc[audit['code_source'] == 'undecodable', 'route_id'].tolist()
    if bad:
        print(f"WARNING: route_id(s) that do not follow the code pattern, left unchanged: {bad}")
    print(f"Audit trail: {REPORT_DIR}/route_enrichment.csv\n\nNext: run fix.py")


if __name__ == "__main__":
    main()
