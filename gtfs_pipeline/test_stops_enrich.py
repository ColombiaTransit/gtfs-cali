"""
Tests stops_enrich.py's ID and spatial matching against fabricated data,
including plausible real-world messiness (leading zeros, unrelated ID
schemes forcing a spatial fallback, an unmatchable outlier stop).

Run: python test_stops_enrich.py
"""
import pandas as pd

import stops_enrich as E


def test_id_match_with_leading_zeros():
    our_stops = pd.DataFrame([
        {"stop_id": "1001", "stop_lat": 3.45, "stop_lon": -76.53},
        {"stop_id": "1002", "stop_lat": 3.46, "stop_lon": -76.52},
        {"stop_id": "1003", "stop_lat": 3.47, "stop_lon": -76.51},
    ])
    ext_df = pd.DataFrame([
        {"CODIGO_PARADA": "01001", "NOMBRE_PARADA": "Terminal Sur", "ext_lat": 3.45, "ext_lon": -76.53},
        {"CODIGO_PARADA": "01002", "NOMBRE_PARADA": "Estacion Central", "ext_lat": 3.46, "ext_lon": -76.52},
        {"CODIGO_PARADA": "01003", "NOMBRE_PARADA": "Estacion Norte", "ext_lat": 3.47, "ext_lon": -76.51},
    ])

    enriched, stats = E.enrich_stops(our_stops, ext_df)
    assert stats["id_matched"] == 3, stats
    assert stats["spatial_matched"] == 0, stats
    assert dict(zip(enriched["stop_id"], enriched["stop_name"])) == {
        "1001": "Terminal Sur", "1002": "Estacion Central", "1003": "Estacion Norte",
    }
    print("test_id_match_with_leading_zeros: PASS")


def test_spatial_fallback_when_ids_dont_overlap():
    # ext IDs are from a totally different numbering scheme -> ID match
    # coverage should be ~0%, forcing the spatial fallback.
    our_stops = pd.DataFrame([
        {"stop_id": "GSTOP-9001", "stop_lat": 3.45000, "stop_lon": -76.53000},
        {"stop_id": "GSTOP-9002", "stop_lat": 3.46000, "stop_lon": -76.52000},
    ])
    ext_df = pd.DataFrame([
        {"COD": "77", "NOMBRE": "Terminal Sur", "ext_lat": 3.45001, "ext_lon": -76.53001},  # ~1.5m away
        {"COD": "78", "NOMBRE": "Estacion Central", "ext_lat": 3.46002, "ext_lon": -76.52002},  # ~3m away
    ])

    enriched, stats = E.enrich_stops(our_stops, ext_df)
    assert stats["id_matched"] == 0, stats
    assert stats["spatial_matched"] == 2, stats
    names = dict(zip(enriched["stop_id"], enriched["stop_name"]))
    assert names["GSTOP-9001"] == "Terminal Sur"
    assert names["GSTOP-9002"] == "Estacion Central"
    print("test_spatial_fallback_when_ids_dont_overlap: PASS")


def test_outlier_stop_stays_unmatched():
    our_stops = pd.DataFrame([
        {"stop_id": "A", "stop_lat": 3.45, "stop_lon": -76.53},
        {"stop_id": "B", "stop_lat": 10.0, "stop_lon": -70.0},  # nowhere near any ext point
    ])
    ext_df = pd.DataFrame([
        {"COD": "1", "NOMBRE": "Terminal Sur", "ext_lat": 3.45001, "ext_lon": -76.53001},
    ])

    enriched, stats = E.enrich_stops(our_stops, ext_df)
    assert stats["spatial_matched"] == 1
    row_b = enriched[enriched["stop_id"] == "B"].iloc[0]
    assert pd.isna(row_b["stop_name"])
    print("test_outlier_stop_stays_unmatched: PASS")


def test_no_name_column_found_returns_unchanged():
    our_stops = pd.DataFrame([{"stop_id": "A", "stop_lat": 3.45, "stop_lon": -76.53}])
    ext_df = pd.DataFrame([{"SOME_CODE": "1", "ext_lat": 3.45, "ext_lon": -76.53}])
    enriched, stats = E.enrich_stops(our_stops, ext_df)
    assert stats["id_matched"] == 0 and stats["spatial_matched"] == 0
    assert "stop_name" not in enriched.columns or enriched["stop_name"].isna().all()
    print("test_no_name_column_found_returns_unchanged: PASS")


def test_real_ptosparadas_schema_prefers_direccion_over_blank_descrip():
    """Reproduces the live ptosparadas schema exactly: STOPID (real join
    key), DIRECCION (populated every row), DESCRIP (blank ' ' on almost
    every row). Must NOT pick DESCRIP as the name source, and should
    combine DIRECCION with a non-blank COMPLEMENT when present."""
    our_stops = pd.DataFrame([
        {"stop_id": "519182", "stop_lat": 3.45845079690317, "stop_lon": -76.5497608288953},
        {"stop_id": "519183", "stop_lat": 3.46018139924794, "stop_lon": -76.5517154457174},
    ])
    ext_df = pd.DataFrame([
        {"FID": 1, "STOPID": 519182, "DIRECCION": "Av 15 Oe entre Cl 7 Oe y 8 Oe",
         "COMPLEMENT": "Bajo Aguacatal", "DESCRIP": " ", "T_PARADA": "PARADA EXTERNA",
         "ext_lat": 3.45845079690317, "ext_lon": -76.5497608288953},
        {"FID": 2, "STOPID": 519183, "DIRECCION": "Av 15 Oe con Cl 9 Oe_1",
         "COMPLEMENT": " ", "DESCRIP": " ", "T_PARADA": "PARADA EXTERNA",
         "ext_lat": 3.46018139924794, "ext_lon": -76.5517154457174},
    ])

    enriched, stats = E.enrich_stops(our_stops, ext_df)
    assert stats["id_matched"] == 2, stats
    assert stats["id_column_used"] == "STOPID", stats
    names = dict(zip(enriched["stop_id"], enriched["stop_name"]))
    # first stop: DIRECCION + non-blank COMPLEMENT combined
    assert names["519182"] == "Av 15 Oe entre Cl 7 Oe y 8 Oe (Bajo Aguacatal)", names
    # second stop: COMPLEMENT is blank -> DIRECCION alone, no stray "()"
    assert names["519183"] == "Av 15 Oe con Cl 9 Oe_1", names
    print("test_real_ptosparadas_schema_prefers_direccion_over_blank_descrip: PASS")


if __name__ == "__main__":
    test_id_match_with_leading_zeros()
    test_spatial_fallback_when_ids_dont_overlap()
    test_outlier_stop_stays_unmatched()
    test_no_name_column_found_returns_unchanged()
    test_real_ptosparadas_schema_prefers_direccion_over_blank_descrip()
    print("\nAll stops_enrich.py tests passed.")
