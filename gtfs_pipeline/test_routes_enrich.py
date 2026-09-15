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


def _straight_line(lat0, lon0, lat1, lon1, n=15):
    return [(lat0 + (lat1 - lat0) * i / (n - 1), lon0 + (lon1 - lon0) * i / (n - 1)) for i in range(n)]


def test_geometry_fallback_when_id_schemes_dont_match():
    """Reproduces the real discovery: our route_id is a plain internal
    integer (e.g. '112') from the GTFS FeatureServer, completely unrelated
    to rutas' letter-coded RUTA ('A01A'). ID matching must score ~0% and
    correctly trigger the geometry fallback, which should still find the
    right route by path shape and also fix route_short_name to the real
    letter code."""
    our_routes = pd.DataFrame([
        {"route_id": "112", "agency_id": "MIO", "route_short_name": "112",
         "route_long_name": "", "route_type": "3"},
        {"route_id": "998", "agency_id": "MIO", "route_short_name": "998",
         "route_long_name": "", "route_type": "3"},  # no nearby rutas geometry at all
    ])

    route_112_path = _straight_line(3.40, -76.53, 3.45, -76.50)
    shapes_rows = []
    for seq, (lat, lon) in enumerate(route_112_path, start=1):
        shapes_rows.append({"shape_id": "SHP-112", "shape_pt_lat": lat, "shape_pt_lon": lon, "shape_pt_sequence": seq})
    shapes_df = pd.DataFrame(shapes_rows)
    trips_df = pd.DataFrame([{"route_id": "112", "shape_id": "SHP-112"}])

    ext_df = pd.DataFrame([
        {"FID": 1, "RUTA": "A01A", "NOMBRE": "ESTACIÓN SAN BOSCO - CAM - CENTRO",
         "_geom_path": [(lat + 0.00005, lon) for lat, lon in route_112_path]},  # ~5.5m off - the real match
        {"FID": 2, "RUTA": "T14", "NOMBRE": "UNIVERSIDADES - 7 DE AGOSTO",
         "_geom_path": _straight_line(3.10, -76.90, 3.12, -76.88)},  # far away, wrong route
    ])

    enriched, stats = RE.enrich_routes(our_routes, ext_df, shapes_df=shapes_df, trips_df=trips_df)
    assert stats["method"] == "geometry", stats
    row_112 = enriched[enriched["route_id"] == "112"].iloc[0]
    assert row_112["route_long_name"] == "ESTACIÓN SAN BOSCO - CAM - CENTRO", row_112.to_dict()
    assert row_112["route_short_name"] == "A01", row_112.to_dict()  # real letter code, not "112"

    row_998 = enriched[enriched["route_id"] == "998"].iloc[0]
    assert pd.isna(row_998["route_long_name"]) or row_998["route_long_name"] == "", row_998.to_dict()
    assert row_998["route_short_name"] == "998"  # untouched - no shape data for this route at all
    print("test_geometry_fallback_when_id_schemes_dont_match: PASS")


def test_geometry_fallback_skips_untrustworthy_matches():
    """A route whose only nearby candidate is still far beyond the trust
    threshold should be left alone, not assigned a wrong name."""
    our_routes = pd.DataFrame([
        {"route_id": "500", "agency_id": "MIO", "route_short_name": "500",
         "route_long_name": "", "route_type": "3"},
    ])
    our_path = _straight_line(3.40, -76.53, 3.45, -76.50)
    shapes_df = pd.DataFrame([
        {"shape_id": "SHP-500", "shape_pt_lat": lat, "shape_pt_lon": lon, "shape_pt_sequence": i}
        for i, (lat, lon) in enumerate(our_path, start=1)
    ])
    trips_df = pd.DataFrame([{"route_id": "500", "shape_id": "SHP-500"}])
    # candidate is within the 5km centroid prefilter but 500m+ off the actual path
    offset_path = [(lat + 0.005, lon) for lat, lon in our_path]
    ext_df = pd.DataFrame([
        {"FID": 1, "RUTA": "A99", "NOMBRE": "SHOULD NOT BE USED", "_geom_path": offset_path},
    ])
    enriched, stats = RE.enrich_routes(our_routes, ext_df, shapes_df=shapes_df, trips_df=trips_df)
    row = enriched[enriched["route_id"] == "500"].iloc[0]
    assert pd.isna(row["route_long_name"]) or row["route_long_name"] == "", row.to_dict()
    assert row["route_short_name"] == "500"
    print("test_geometry_fallback_skips_untrustworthy_matches: PASS")


def test_filter_to_normal_variant_excludes_special_occasion_rows():
    ext_df = pd.DataFrame([
        {"RUTA": "A01A", "VARIANTE": "NORMAL", "NOMBRE": "Everyday route"},
        {"RUTA": "A01A", "VARIANTE": "CICLOVIA", "NOMBRE": "Sunday ciclovia detour"},
        {"RUTA": "A01A", "VARIANTE": "DESVIO", "NOMBRE": "Hourly detour"},
        {"RUTA": "A02", "VARIANTE": "normal", "NOMBRE": "Lowercase should still count"},
        {"RUTA": "A03", "NOMBRE": "No VARIANTE value at all - kept defensively"},
    ])
    filtered = RE.filter_to_normal_variant(ext_df)
    assert set(filtered["NOMBRE"]) == {
        "Everyday route", "Lowercase should still count",
        "No VARIANTE value at all - kept defensively",
    }, set(filtered["NOMBRE"])
    print("test_filter_to_normal_variant_excludes_special_occasion_rows: PASS")


def test_ciclovia_variant_never_wins_over_normal_route():
    """A CICLOVIA row sharing the exact same RUTA and (deliberately, to
    stress-test) an even closer geometry than the NORMAL row must still
    lose - it should never be picked as the route's identity."""
    our_routes = pd.DataFrame([
        {"route_id": "112", "agency_id": "MIO", "route_short_name": "112",
         "route_long_name": "", "route_type": "3"},
    ])
    our_path = _straight_line(3.40, -76.53, 3.45, -76.50)
    shapes_df = pd.DataFrame([
        {"shape_id": "SHP-112", "shape_pt_lat": lat, "shape_pt_lon": lon, "shape_pt_sequence": i}
        for i, (lat, lon) in enumerate(our_path, start=1)
    ])
    trips_df = pd.DataFrame([{"route_id": "112", "shape_id": "SHP-112"}])

    ext_df = pd.DataFrame([
        {"FID": 1, "RUTA": "A01A", "VARIANTE": "NORMAL",
         "NOMBRE": "ESTACIÓN SAN BOSCO - CAM - CENTRO",
         "_geom_path": [(lat + 0.0002, lon) for lat, lon in our_path]},  # ~22m off
        {"FID": 2, "RUTA": "A01A", "VARIANTE": "CICLOVIA",
         "NOMBRE": "SUNDAY CICLOVIA DETOUR - WRONG FOR EVERYDAY GTFS",
         "_geom_path": our_path},  # 0m off - deliberately the "closer" geometry
    ])

    enriched, stats = RE.enrich_routes(our_routes, ext_df, shapes_df=shapes_df, trips_df=trips_df)
    row = enriched[enriched["route_id"] == "112"].iloc[0]
    assert row["route_long_name"] == "ESTACIÓN SAN BOSCO - CAM - CENTRO", row.to_dict()
    print("test_ciclovia_variant_never_wins_over_normal_route: PASS")


if __name__ == "__main__":
    test_base_route_code_strips_variant_letter()
    test_enrich_routes_matches_and_picks_primary_variant()
    test_enrich_routes_prefers_exact_base_code_row()
    test_low_coverage_bails_out_safely()
    test_missing_name_column_returns_unchanged()
    test_geometry_fallback_when_id_schemes_dont_match()
    test_geometry_fallback_skips_untrustworthy_matches()
    test_filter_to_normal_variant_excludes_special_occasion_rows()
    test_ciclovia_variant_never_wins_over_normal_route()
    print("\nAll routes_enrich.py tests passed.")
