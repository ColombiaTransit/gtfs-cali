"""
Tests diagnose_service_ids.py's summary logic against fabricated data.

Run: python test_diagnose_service_ids.py
"""
import pandas as pd

import diagnose_service_ids as DSI


def test_trip_and_route_counts():
    trips = pd.DataFrame([
        {"route_id": "112", "service_id": "8099041", "trip_id": "T1"},
        {"route_id": "112", "service_id": "8099041", "trip_id": "T2"},
        {"route_id": "113", "service_id": "8099041", "trip_id": "T3"},
        {"route_id": "112", "service_id": "8099148", "trip_id": "T4"},
    ])
    summary = DSI.summarize_service_ids(trips)
    by_sid = {row["service_id"]: row for _, row in summary.iterrows()}

    assert by_sid["8099041"]["n_trips"] == 3
    assert by_sid["8099041"]["n_routes"] == 2
    assert by_sid["8099148"]["n_trips"] == 1
    assert by_sid["8099148"]["n_routes"] == 1
    print("test_trip_and_route_counts: PASS")


def test_exception_weekday_and_festivo_detection():
    trips = pd.DataFrame([
        {"route_id": "112", "service_id": "8099041", "trip_id": "T1"},
        {"route_id": "112", "service_id": "8099148", "trip_id": "T2"},
    ])
    calendar_dates = pd.DataFrame([
        # Jan 1 2026 is a real festivo (Año Nuevo, Thursday)
        {"service_id": "8099041", "date": "20260101", "exception_type": "2"},
        # a plain Tuesday, not a festivo
        {"service_id": "8099041", "date": "20260106", "exception_type": "2"},
        # 8099148 has no exceptions at all
    ])
    summary = DSI.summarize_service_ids(trips, calendar_dates)
    by_sid = {row["service_id"]: row for _, row in summary.iterrows()}

    assert by_sid["8099041"]["n_exceptions"] == 2
    assert "Thursday" in by_sid["8099041"]["exception_weekdays"]
    assert by_sid["8099041"]["n_exceptions_on_festivo"] == 1

    assert by_sid["8099148"]["n_exceptions"] == 0
    print("test_exception_weekday_and_festivo_detection: PASS")


def test_format_report_runs_without_error():
    trips = pd.DataFrame([
        {"route_id": "112", "service_id": "8099041", "trip_id": "T1"},
    ])
    calendar_dates = pd.DataFrame([
        {"service_id": "8099041", "date": "20260101", "exception_type": "2"},
    ])
    summary = DSI.summarize_service_ids(trips, calendar_dates)
    report = DSI.format_report(summary)
    assert "8099041" in report
    assert "Thursday" in report
    print("test_format_report_runs_without_error: PASS")


def test_empty_trips_handled_gracefully():
    summary = DSI.summarize_service_ids(None)
    assert summary.empty
    report = DSI.format_report(summary)
    assert "No trips.txt data" in report
    print("test_empty_trips_handled_gracefully: PASS")


def test_stale_2025_exceptions_flagged_in_report():
    """Reproduces the real scenario: the committed feed's calendar_dates.txt
    turned out to be entirely 2025 dates. The report must call this out up
    front, not just quietly print the (misleadingly old) evidence."""
    trips = pd.DataFrame([
        {"route_id": "112", "service_id": "8099041", "trip_id": "T1"},
    ])
    calendar_dates = pd.DataFrame([
        {"service_id": "8099041", "date": "20250315", "exception_type": "2"},
        {"service_id": "8099041", "date": "20250601", "exception_type": "1"},
    ])
    summary = DSI.summarize_service_ids(trips, calendar_dates)
    report = DSI.format_report(summary)
    assert "STALE" in report
    assert "2025" in report
    print("test_stale_2025_exceptions_flagged_in_report: PASS")


if __name__ == "__main__":
    test_trip_and_route_counts()
    test_exception_weekday_and_festivo_detection()
    test_format_report_runs_without_error()
    test_empty_trips_handled_gracefully()
    test_stale_2025_exceptions_flagged_in_report()
    print("\nAll diagnose_service_ids.py tests passed.")
