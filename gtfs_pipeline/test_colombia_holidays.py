"""
Tests colombia_holidays.py against real, independently-confirmed 2026
festivo dates (verified via news sources reporting the official 2026
Colombian holiday calendar), plus structural checks (weekday correctness,
count, Easter-relative computation).

Run: python test_colombia_holidays.py
"""
import datetime

import colombia_holidays as CH


def test_easter_2026():
    # Confirmed: Domingo de Resurrección 2026 = April 5, 2026.
    assert CH.easter_sunday(2026) == datetime.date(2026, 4, 5)
    print("test_easter_2026: PASS")


def test_fixed_holidays_never_move():
    holidays = CH.colombia_holidays_for_year(2026)
    assert holidays[datetime.date(2026, 1, 1)] == "Año Nuevo"
    assert holidays[datetime.date(2026, 5, 1)] == "Día del Trabajo"
    assert holidays[datetime.date(2026, 7, 20)] == "Día de la Independencia"
    assert holidays[datetime.date(2026, 8, 7)] == "Batalla de Boyacá"
    assert holidays[datetime.date(2026, 12, 8)] == "Inmaculada Concepción"
    assert holidays[datetime.date(2026, 12, 25)] == "Navidad"
    print("test_fixed_holidays_never_move: PASS")


def test_confirmed_real_2026_movable_dates():
    """These exact dates were independently confirmed via news reporting
    on the official 2026 Colombian holiday calendar - not just internally
    self-consistent, actually matched against reality."""
    holidays = CH.colombia_holidays_for_year(2026)
    assert datetime.date(2026, 1, 12) in holidays  # Reyes Magos, moved from Jan 6 (Tue)
    assert holidays[datetime.date(2026, 1, 12)] == "Reyes Magos"
    assert datetime.date(2026, 3, 23) in holidays  # San José, moved from Mar 19 (Thu)
    assert holidays[datetime.date(2026, 3, 23)] == "San José"
    assert datetime.date(2026, 11, 2) in holidays  # Todos los Santos, moved from Nov 1 (Sun)
    assert holidays[datetime.date(2026, 11, 2)] == "Todos los Santos"
    print("test_confirmed_real_2026_movable_dates: PASS")


def test_all_movable_holidays_land_on_monday():
    holidays = CH.colombia_holidays_for_year(2026)
    movable_names = {name for _, _, name in CH.MOVABLE_TO_MONDAY_FIXED_DATE} | \
                     {name for _, name in CH.EASTER_RELATIVE_MOVABLE}
    for d, name in holidays.items():
        if name in movable_names:
            assert d.weekday() == 0, f"{name} on {d} is not a Monday (weekday={d.weekday()})"
    print("test_all_movable_holidays_land_on_monday: PASS")


def test_semana_santa_relative_to_easter():
    holidays = CH.colombia_holidays_for_year(2026)
    easter = datetime.date(2026, 4, 5)
    jueves_santo = easter - datetime.timedelta(days=3)
    viernes_santo = easter - datetime.timedelta(days=2)
    assert holidays[jueves_santo] == "Jueves Santo"
    assert holidays[viernes_santo] == "Viernes Santo"
    assert jueves_santo.weekday() == 3  # Thursday
    assert viernes_santo.weekday() == 4  # Friday
    print("test_semana_santa_relative_to_easter: PASS")


def test_total_count_is_19_from_2026():
    holidays_2026 = CH.colombia_holidays_for_year(2026)
    assert len(holidays_2026) == 19, f"expected 19 festivos in 2026, got {len(holidays_2026)}: {sorted(holidays_2026)}"
    assert datetime.date(2026, 7, 13) in holidays_2026  # Chiquinquirá, moved from Jul 9 (Thu)
    assert holidays_2026[datetime.date(2026, 7, 13)] == "Día de Nuestra Señora del Rosario de Chiquinquirá"

    # Before 2026, Chiquinquirá must never appear - but note the *unique date*
    # count isn't a fixed 18 every year: two distinct legal holidays can
    # coincidentally shift onto the same calendar date in a given year (e.g.
    # in 2025, San Pedro y San Pablo shifts from Sun Jun 29 -> Mon Jun 30,
    # landing exactly on Sagrado Corazón de Jesús that year - a real
    # coincidence in the Colombian calendar, not a bug). So we only assert
    # an upper bound and the absence of Chiquinquirá, not an exact count.
    holidays_2025 = CH.colombia_holidays_for_year(2025)
    assert len(holidays_2025) <= 18, f"expected at most 18 unique festivo dates before 2026, got {len(holidays_2025)}"
    assert not any("Chiquinquirá" in name for name in holidays_2025.values())
    print("test_total_count_is_19_from_2026: PASS")


def test_holidays_in_range_spans_multiple_years():
    start = datetime.date(2025, 12, 20)
    end = datetime.date(2026, 1, 15)
    holidays = CH.colombia_holidays_in_range(start, end)
    assert datetime.date(2025, 12, 25) in holidays  # from 2025
    assert datetime.date(2026, 1, 1) in holidays    # from 2026
    assert datetime.date(2026, 1, 12) in holidays   # from 2026
    # nothing outside the range
    assert all(start <= d <= end for d in holidays)
    print("test_holidays_in_range_spans_multiple_years: PASS")


def test_gtfs_date_roundtrip():
    d = datetime.date(2026, 3, 15)
    assert CH.format_gtfs_date(d) == "20260315"
    assert CH.parse_gtfs_date("20260315") == d
    print("test_gtfs_date_roundtrip: PASS")


if __name__ == "__main__":
    test_easter_2026()
    test_fixed_holidays_never_move()
    test_confirmed_real_2026_movable_dates()
    test_all_movable_holidays_land_on_monday()
    test_semana_santa_relative_to_easter()
    test_total_count_is_19_from_2026()
    test_holidays_in_range_spans_multiple_years()
    test_gtfs_date_roundtrip()
    print("\nAll colombia_holidays.py tests passed.")
