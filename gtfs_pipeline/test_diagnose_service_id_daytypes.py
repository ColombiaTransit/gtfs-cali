"""
Tests diagnose_service_id_daytypes.py against fabricated data, including
the core scenario: does the vote aggregation correctly identify a
service_id's day-type when the pattern holds consistently across
multiple different routes, and correctly show LOW confidence when it
doesn't.

Run: python test_diagnose_service_id_daytypes.py
"""
import pandas as pd

import diagnose_service_id_daytypes as DST


def test_parse_time_window_formats():
    assert DST.parse_time_window("05:00 - 22:00") == (5 * 3600, 22 * 3600)
    assert DST.parse_time_window("05:00-22:00") == (5 * 3600, 22 * 3600)
    assert DST.parse_time_window("5:00 AM - 10:00 PM") == (5 * 3600, 22 * 3600)
    assert DST.parse_time_window("5:00am-10:00pm") == (5 * 3600, 22 * 3600)
    assert DST.parse_time_window("12:00 AM - 12:00 PM") == (0, 12 * 3600)
    assert DST.parse_time_window(None) is None
    assert DST.parse_time_window("") is None
    assert DST.parse_time_window("N/A") is None
    assert DST.parse_time_window("just one 05:00 token") is None
    print("test_parse_time_window_formats: PASS")


def test_gtfs_time_to_seconds_handles_post_midnight():
    assert DST.gtfs_time_to_seconds("06:15:00") == 6 * 3600 + 15 * 60
    assert DST.gtfs_time_to_seconds("25:30:00") == 25 * 3600 + 30 * 60
    print("test_gtfs_time_to_seconds_handles_post_midnight: PASS")


def test_extract_route_daytype_windows_skips_ciclovia():
    ext_df = pd.DataFrame([
        {"RUTA": "A01A", "VARIANTE": "NORMAL", "HABIL": "05:00 - 22:00",
         "SABADO": "06:00 - 21:00", "DOM_FEST": "07:00 - 19:00"},
        {"RUTA": "A01A", "VARIANTE": "CICLOVIA", "HABIL": "", "SABADO": "",
         "DOM_FEST": "06:00 - 12:00"},  # should be excluded
    ])
    windows = DST.extract_route_daytype_windows(ext_df)
    assert windows["A01"]["HABIL"] == (5 * 3600, 22 * 3600)
    assert windows["A01"]["DOM_FEST"] == (7 * 3600, 19 * 3600)  # NOT the ciclovia 6-12 window
    print("test_extract_route_daytype_windows_skips_ciclovia: PASS")


def test_compute_trip_spans():
    trips = pd.DataFrame([
        {"trip_id": "T1", "route_id": "112", "service_id": "S1"},
        {"trip_id": "T2", "route_id": "112", "service_id": "S1"},
    ])
    stop_times = pd.DataFrame([
        {"trip_id": "T1", "departure_time": "05:10:00"},
        {"trip_id": "T1", "departure_time": "05:40:00"},
        {"trip_id": "T2", "departure_time": "21:20:00"},
        {"trip_id": "T2", "departure_time": "21:50:00"},
    ])
    spans = DST.compute_trip_spans(trips, stop_times)
    row = spans.iloc[0]
    assert row["route_id"] == "112" and row["service_id"] == "S1"
    assert row["span_start_sec"] == 5 * 3600 + 10 * 60
    assert row["span_end_sec"] == 21 * 3600 + 50 * 60
    assert row["n_trips"] == 2
    print("test_compute_trip_spans: PASS")


def test_score_window_fit():
    window = (5 * 3600, 22 * 3600)
    good = DST.score_window_fit(5 * 3600 + 5 * 60, 21 * 3600 + 55 * 60, window)
    assert good["contained"] is True
    assert good["diff_sec"] < 20 * 60

    bad = DST.score_window_fit(7 * 3600, 19 * 3600, window)  # DOM_FEST-shaped span vs HABIL window
    assert bad["diff_sec"] > good["diff_sec"]

    assert DST.score_window_fit(5 * 3600, 22 * 3600, None) is None
    print("test_score_window_fit: PASS")


def _make_windows():
    return {
        "A01": {"HABIL": (5 * 3600, 22 * 3600), "SABADO": (6 * 3600, 21 * 3600),
                "DOM_FEST": (7 * 3600, 19 * 3600)},
        "A02": {"HABIL": (5 * 3600 + 30 * 60, 22 * 3600 + 30 * 60),
                "SABADO": (6 * 3600 + 30 * 60, 21 * 3600 + 30 * 60),
                "DOM_FEST": (7 * 3600 + 30 * 60, 19 * 3600 + 30 * 60)},
    }


def test_analyze_and_aggregate_consistent_pattern_gives_high_confidence():
    """Core scenario: service_id 'S_WEEKDAY' consistently fits the HABIL
    window on BOTH route A01 and A02 -> should get high confidence.
    service_id 'S_SUNDAY' consistently fits DOM_FEST on both -> same."""
    windows = _make_windows()
    route_map = {"112": "A01", "113": "A02"}

    spans = pd.DataFrame([
        {"route_id": "112", "service_id": "S_WEEKDAY", "span_start_sec": 5 * 3600 + 5 * 60,
         "span_end_sec": 21 * 3600 + 55 * 60, "n_trips": 40},
        {"route_id": "113", "service_id": "S_WEEKDAY", "span_start_sec": 5 * 3600 + 35 * 60,
         "span_end_sec": 22 * 3600 + 25 * 60, "n_trips": 38},
        {"route_id": "112", "service_id": "S_SUNDAY", "span_start_sec": 7 * 3600 + 5 * 60,
         "span_end_sec": 18 * 3600 + 55 * 60, "n_trips": 10},
        {"route_id": "113", "service_id": "S_SUNDAY", "span_start_sec": 7 * 3600 + 35 * 60,
         "span_end_sec": 19 * 3600 + 25 * 60, "n_trips": 9},
    ])

    analysis = DST.analyze(windows, spans, route_map)
    agg = DST.aggregate_by_service_id(analysis)
    by_sid = {row["service_id"]: row for _, row in agg.iterrows()}

    assert by_sid["S_WEEKDAY"]["top_daytype"] == "HABIL"
    assert by_sid["S_WEEKDAY"]["confidence"] == 1.0
    assert by_sid["S_SUNDAY"]["top_daytype"] == "DOM_FEST"
    assert by_sid["S_SUNDAY"]["confidence"] == 1.0
    print("test_analyze_and_aggregate_consistent_pattern_gives_high_confidence: PASS")


def test_inconsistent_pattern_gives_low_confidence():
    """A service_id whose best-fit day-type FLIPS between routes should
    score low confidence, not be reported as if it were reliable evidence."""
    windows = _make_windows()
    route_map = {"112": "A01", "113": "A02"}

    spans = pd.DataFrame([
        {"route_id": "112", "service_id": "S_MYSTERY", "span_start_sec": 5 * 3600,
         "span_end_sec": 22 * 3600, "n_trips": 20},
        {"route_id": "113", "service_id": "S_MYSTERY", "span_start_sec": 7 * 3600 + 30 * 60,
         "span_end_sec": 19 * 3600 + 30 * 60, "n_trips": 20},
    ])
    analysis = DST.analyze(windows, spans, route_map)
    agg = DST.aggregate_by_service_id(analysis)
    row = agg[agg["service_id"] == "S_MYSTERY"].iloc[0]
    assert row["confidence"] == 0.5, row.to_dict()
    print("test_inconsistent_pattern_gives_low_confidence: PASS")


def test_route_with_no_published_windows_yields_no_evidence():
    windows = _make_windows()
    route_map = {"999": "UNKNOWN_ROUTE"}
    spans = pd.DataFrame([
        {"route_id": "999", "service_id": "S1", "span_start_sec": 5 * 3600,
         "span_end_sec": 22 * 3600, "n_trips": 5},
    ])
    analysis = DST.analyze(windows, spans, route_map)
    assert analysis.iloc[0]["best_daytype"] is None
    agg = DST.aggregate_by_service_id(analysis)
    assert agg.iloc[0]["top_daytype"] is None
    assert agg.iloc[0]["n_routes_with_evidence"] == 0
    print("test_route_with_no_published_windows_yields_no_evidence: PASS")


def test_format_report_runs_without_error():
    windows = _make_windows()
    route_map = {"112": "A01"}
    spans = pd.DataFrame([
        {"route_id": "112", "service_id": "S1", "span_start_sec": 5 * 3600,
         "span_end_sec": 22 * 3600, "n_trips": 5},
    ])
    analysis = DST.analyze(windows, spans, route_map)
    agg = DST.aggregate_by_service_id(analysis)
    report = DST.format_report(agg, analysis)
    assert "S1" in report
    assert "HABIL" in report
    print("test_format_report_runs_without_error: PASS")


if __name__ == "__main__":
    test_parse_time_window_formats()
    test_gtfs_time_to_seconds_handles_post_midnight()
    test_extract_route_daytype_windows_skips_ciclovia()
    test_compute_trip_spans()
    test_score_window_fit()
    test_analyze_and_aggregate_consistent_pattern_gives_high_confidence()
    test_inconsistent_pattern_gives_low_confidence()
    test_route_with_no_published_windows_yields_no_evidence()
    test_format_report_runs_without_error()
    print("\nAll diagnose_service_id_daytypes.py tests passed.")
