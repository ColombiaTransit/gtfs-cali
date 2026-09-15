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


if __name__ == "__main__":
    test_build_temporary_calendar_flags()
    test_unknown_service_id_raises()
    test_cross_check_reports_mismatch_honestly()
    test_cross_check_reports_full_match()
    test_add_festivo_exceptions_jan_2026()
    test_add_festivo_exceptions_respects_existing_calendar_dates()
    test_saturday_festivo_suppresses_sabado()
    print("\nAll temp_calendar.py tests passed.")
