"""
Tests temp_calendar.py: the HABIL/SABADO/DOM_FEST temporary calendar
builder and the festivo add/suppress exception logic.

Run: python test_temp_calendar.py
"""
import pandas as pd

import temp_calendar as TC


def test_build_temporary_calendar_flags():
    cal = TC.build_temporary_calendar("20260101", "20261231")
    by_sid = {row["service_id"]: row for _, row in cal.iterrows()}

    assert by_sid["HABIL"]["monday"] == "1"
    assert by_sid["HABIL"]["friday"] == "1"
    assert by_sid["HABIL"]["saturday"] == "0"
    assert by_sid["HABIL"]["sunday"] == "0"

    assert by_sid["SABADO"]["saturday"] == "1"
    assert by_sid["SABADO"]["sunday"] == "0"
    assert by_sid["SABADO"]["monday"] == "0"

    assert by_sid["DOM_FEST"]["sunday"] == "1"
    assert by_sid["DOM_FEST"]["saturday"] == "0"
    assert by_sid["DOM_FEST"]["monday"] == "0"

    for sid in by_sid:
        assert by_sid[sid]["start_date"] == "20260101"
        assert by_sid[sid]["end_date"] == "20261231"
    print("test_build_temporary_calendar_flags: PASS")


def test_unknown_service_id_raises():
    try:
        TC.build_temporary_calendar("20260101", "20261231", service_ids=("BOGUS",))
        assert False, "expected ValueError"
    except ValueError:
        pass
    print("test_unknown_service_id_raises: PASS")


def test_cross_check_reports_mismatch_honestly():
    matched, unmatched_real, unmatched_dt = TC.cross_check_against_real_service_ids(
        {"112-WK-1", "112-SAT-1", "112-SUN-1"}
    )
    assert matched == set(), matched
    assert unmatched_real == {"112-WK-1", "112-SAT-1", "112-SUN-1"}
    assert unmatched_dt == {"HABIL", "SABADO", "DOM_FEST"}
    print("test_cross_check_reports_mismatch_honestly: PASS")


def test_cross_check_reports_full_match():
    matched, unmatched_real, unmatched_dt = TC.cross_check_against_real_service_ids(
        {"HABIL", "SABADO", "DOM_FEST"}
    )
    assert matched == {"HABIL", "SABADO", "DOM_FEST"}
    assert unmatched_real == set()
    assert unmatched_dt == set()
    print("test_cross_check_reports_full_match: PASS")


def test_add_festivo_exceptions_jan_2026():
    """Jan 1 2026 = Thursday (Año Nuevo), Jan 12 2026 = Monday (Reyes Magos) -
    only these two festivos fall within a Jan 1 - Feb 1 window."""
    cal = TC.build_temporary_calendar("20260101", "20260201")
    result = TC.add_festivo_exceptions(cal)
    pairs = {(r["service_id"], r["date"], r["exception_type"]) for _, r in result.iterrows()}

    assert ("HABIL", "20260101", "2") in pairs, pairs
    assert ("DOM_FEST", "20260101", "1") in pairs, pairs
    assert ("HABIL", "20260112", "2") in pairs, pairs
    assert ("DOM_FEST", "20260112", "1") in pairs, pairs
    assert not any(sid == "SABADO" for sid, _, _ in pairs), pairs
    assert len(result) == 4, len(result)
    print("test_add_festivo_exceptions_jan_2026: PASS")


def test_add_festivo_exceptions_respects_existing_calendar_dates():
    cal = TC.build_temporary_calendar("20260101", "20260115")
    existing = pd.DataFrame([
        {"service_id": "HABIL", "date": "20260101", "exception_type": "1"},
    ])
    result = TC.add_festivo_exceptions(cal, existing)
    jan1_habil = result[(result["service_id"] == "HABIL") & (result["date"] == "20260101")]
    assert len(jan1_habil) == 1
    assert jan1_habil.iloc[0]["exception_type"] == "1"
    print("test_add_festivo_exceptions_respects_existing_calendar_dates: PASS")


def test_saturday_festivo_suppresses_sabado():
    """Aug 7 (Batalla de Boyacá, fixed, never moved) falls on a Saturday in
    2027 - use that to confirm SABADO gets suppressed too, not just HABIL."""
    import datetime
    d = datetime.date(2027, 8, 7)
    assert d.weekday() == 5, f"test assumption wrong: Aug 7 2027 is not a Saturday ({d.strftime('%A')})"

    cal = TC.build_temporary_calendar("20270801", "20270810")
    result = TC.add_festivo_exceptions(cal)
    pairs = {(r["service_id"], r["date"], r["exception_type"]) for _, r in result.iterrows()}
    assert ("SABADO", "20270807", "2") in pairs, pairs
    assert ("DOM_FEST", "20270807", "1") in pairs, pairs
    assert not any(date == "20270807" and sid == "HABIL" for sid, date, _ in pairs), pairs
    print("test_saturday_festivo_suppresses_sabado: PASS")


def test_stale_existing_exceptions_flagged_but_kept_by_default():
    """Reproduces the real scenario: the committed feed's calendar_dates.txt
    turned out to contain 2025 dates while building a calendar for 2026.
    These must be flagged as stale, NOT silently treated as current, but
    kept in the output by default (never silently discard real data)."""
    cal = TC.build_temporary_calendar("20260101", "20261231")
    stale_2025 = pd.DataFrame([
        {"service_id": "8099041", "date": "20250315", "exception_type": "2"},
        {"service_id": "8099148", "date": "20250601", "exception_type": "1"},
    ])
    result = TC.add_festivo_exceptions(cal, stale_2025)
    # stale rows are still present in the output (default = kept, just warned)
    assert ("8099041", "20250315", "2") in {(r["service_id"], r["date"], r["exception_type"]) for _, r in result.iterrows()}
    # real 2026 festivo exceptions were still generated correctly regardless
    assert ("HABIL", "20260101", "2") in {(r["service_id"], r["date"], r["exception_type"]) for _, r in result.iterrows()}
    print("test_stale_existing_exceptions_flagged_but_kept_by_default: PASS")


def test_stale_existing_exceptions_excluded_when_requested():
    cal = TC.build_temporary_calendar("20260101", "20261231")
    stale_2025 = pd.DataFrame([
        {"service_id": "8099041", "date": "20250315", "exception_type": "2"},
    ])
    result = TC.add_festivo_exceptions(cal, stale_2025, exclude_stale_existing=True)
    pairs = {(r["service_id"], r["date"]) for _, r in result.iterrows()}
    assert ("8099041", "20250315") not in pairs, pairs
    # the 2026 festivo generation still happened
    assert ("HABIL", "20260101") in pairs, pairs
    print("test_stale_existing_exceptions_excluded_when_requested: PASS")


def test_mostly_in_range_existing_data_not_flagged_as_stale():
    """If only a handful of rows fall outside the range (not all of them),
    that's not wholesale staleness - don't raise the loud warning."""
    cal = TC.build_temporary_calendar("20260101", "20261231")
    mostly_current = pd.DataFrame([
        {"service_id": "X", "date": "20260615", "exception_type": "1"},
        {"service_id": "X", "date": "20260820", "exception_type": "1"},
        {"service_id": "X", "date": "20251231", "exception_type": "1"},  # one stray old row
    ])
    result = TC.add_festivo_exceptions(cal, mostly_current)
    pairs = {(r["service_id"], r["date"]) for _, r in result.iterrows()}
    # all three rows kept (no exclusion requested, and not wholesale-stale anyway)
    assert ("X", "20260615") in pairs
    assert ("X", "20251231") in pairs
    print("test_mostly_in_range_existing_data_not_flagged_as_stale: PASS")


if __name__ == "__main__":
    test_build_temporary_calendar_flags()
    test_unknown_service_id_raises()
    test_cross_check_reports_mismatch_honestly()
    test_cross_check_reports_full_match()
    test_add_festivo_exceptions_jan_2026()
    test_add_festivo_exceptions_respects_existing_calendar_dates()
    test_saturday_festivo_suppresses_sabado()
    test_stale_existing_exceptions_flagged_but_kept_by_default()
    test_stale_existing_exceptions_excluded_when_requested()
    test_mostly_in_range_existing_data_not_flagged_as_stale()
    print("\nAll temp_calendar.py tests passed.")
