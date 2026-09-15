"""
Tests routes_enrich.py's base-code derivation and matching logic against
fabricated data shaped exactly like the live "rutas" service (RUTA/NOMBRE
fields, variant letters A01A/A01B, etc).

Run: python test_routes_enrich.py
"""
import pandas as pd

import routes_enrich as RE


def test_base_route_code_strips_variant_letter():
    assert RE.base_route_code("A01A") == "A01"
    assert RE.base_route_code("A01B") == "A01"
    assert RE.base_route_code("A11B") == "A11"
    assert RE.base_route_code("A12C") == "A12"
    assert RE.base_route_code("A02") == "A02"  # no suffix to strip
    assert RE.base_route_code(None) is None
    print("test_base_route_code_strips_variant_letter: PASS")


def test_enrich_routes_matches_and_picks_primary_variant():
    our_routes = pd.DataFrame([
        {"route_id": "A01", "agency_id": "MIO", "route_short_name": "A01",
         "route_long_name": "", "route_type": "3"},
        {"route_id": "A02", "agency_id": "MIO", "route_short_name": "A02",
         "route_long_name": "", "route_type": "3"},
    ])
    ext_df = pd.DataFrame([
        {"FID": 1, "RUTA": "A01A", "NOMBRE": "ESTACIÓN SAN BOSCO - CAM - CENTRO",
         "SERVICIO": "ALIMENTADOR"},
        {"FID": 2, "RUTA": "A01B", "NOMBRE": "CENTRO - SAN NICÓLAS - PRIMITIVO CRESPO",
         "SERVICIO": "ALIMENTADOR"},
        {"FID": 3, "RUTA": "A02", "NOMBRE": "CENTRO - ZOOLÓGICO - ATENAS",
         "SERVICIO": "ALIMENTADOR"},
    ])

    enriched, stats = RE.enrich_routes(our_routes, ext_df)
    assert stats["matched"] == 2, stats
    names = dict(zip(enriched["route_id"], enriched["route_long_name"]))
    # A01 has no exact "A01" row (only A01A/A01B) -> falls back to first variant found
    assert names["A01"] == "ESTACIÓN SAN BOSCO - CAM - CENTRO", names
    # A02 has an exact-match row -> uses it directly
    assert names["A02"] == "CENTRO - ZOOLÓGICO - ATENAS", names
    print("test_enrich_routes_matches_and_picks_primary_variant: PASS")


def test_enrich_routes_prefers_exact_base_code_row():
    """When both a suffixed variant AND an exact base-code row exist for the
    same route, the exact one should win (it's the 'primary' variant)."""
    our_routes = pd.DataFrame([
        {"route_id": "A12", "agency_id": "MIO", "route_short_name": "A12",
         "route_long_name": "", "route_type": "3"},
    ])
    ext_df = pd.DataFrame([
        {"FID": 1, "RUTA": "A12A", "NOMBRE": "SUFFIXED VARIANT NAME"},
        {"FID": 2, "RUTA": "A12", "NOMBRE": "PRIMARY VARIANT NAME"},
        {"FID": 3, "RUTA": "A12C", "NOMBRE": "ANOTHER SUFFIXED VARIANT"},
    ])
    enriched, stats = RE.enrich_routes(our_routes, ext_df)
    assert enriched.loc[enriched["route_id"] == "A12", "route_long_name"].iloc[0] == "PRIMARY VARIANT NAME"
    print("test_enrich_routes_prefers_exact_base_code_row: PASS")


def test_low_coverage_bails_out_safely():
    """If our route_id values don't match the RUTA scheme at all, don't
    silently produce garbage - bail with 0 matches."""
    our_routes = pd.DataFrame([
        {"route_id": "999-completely-different-scheme", "agency_id": "MIO",
         "route_short_name": "X", "route_long_name": "", "route_type": "3"},
    ])
    ext_df = pd.DataFrame([
        {"FID": 1, "RUTA": "A01A", "NOMBRE": "ESTACIÓN SAN BOSCO - CAM - CENTRO"},
    ])
    enriched, stats = RE.enrich_routes(our_routes, ext_df)
    assert stats["matched"] == 0, stats
    print("test_low_coverage_bails_out_safely: PASS")


def test_missing_name_column_returns_unchanged():
    our_routes = pd.DataFrame([{"route_id": "A01", "route_long_name": ""}])
    ext_df = pd.DataFrame([{"RUTA": "A01A", "SOME_OTHER_FIELD": "x"}])
    enriched, stats = RE.enrich_routes(our_routes, ext_df)
    assert stats["matched"] == 0
    print("test_missing_name_column_returns_unchanged: PASS")


if __name__ == "__main__":
    test_base_route_code_strips_variant_letter()
    test_enrich_routes_matches_and_picks_primary_variant()
    test_enrich_routes_prefers_exact_base_code_row()
    test_low_coverage_bails_out_safely()
    test_missing_name_column_returns_unchanged()
    print("\nAll routes_enrich.py tests passed.")
