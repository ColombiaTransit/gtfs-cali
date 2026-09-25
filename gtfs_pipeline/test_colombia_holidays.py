"""
Tests colombia_holidays.py (now a thin wrapper around the `holidays`
package) against real, independently-confirmed 2026 festivo dates, plus
the specific edge cases already found while this module was hand-rolled:
the brand-new Ley 2578 de 2026 (Chiquinquirá) holiday, and the 2025
San Pedro y San Pablo / Sagrado Corazón date collision.

Run: python test_colombia_holidays.py
"""
import datetime

import colombia_holidays as CH


def test_easter_2026():
    # Confirmed: Domingo de Resurrección 2026 = April 5, 2026.
    assert CH.easter_sunday(2026) == datetime.date(2026, 4, 5)
    print("test_easter_2026: PASS")


def test_fixed_holidays_never_move():
    """These six never shift to Monday under Ley Emiliani - confirmed via
    2025, where Jul 20 fell on a Sunday and correctly stayed there rather
    than moving."""
    holidays_2026 = CH.colombia_holidays_for_year(2026)
    for month, day in [(1, 1), (5, 1), (7, 20), (8, 7), (12, 8), (12, 25)]:
        assert datetime.date(2026, month, day) in holidays_2026, (month, day)

    holidays_2025 = CH.colombia_holidays_for_year(2025)
    assert datetime.date(2025, 7, 20) in holidays_2025  # a Sunday in 2025, not moved
    assert datetime.date(2025, 7, 20).weekday() == 6  # Sunday
    print("test_fixed_holidays_never_move: PASS")


def test_confirmed_real_2026_movable_dates():
    """These exact dates were independently confirmed via news reporting
    on the official 2026 Colombian holiday calendar."""
    holidays = CH.colombia_holidays_for_year(2026)
    assert datetime.date(2026, 1, 12) in holidays  # Reyes Magos, moved from Jan 6 (Tue)
    assert "Reyes" in holidays[datetime.date(2026, 1, 12)]
    assert datetime.date(2026, 3, 23) in holidays  # San José, moved from Mar 19 (Thu)
    assert "José" in holidays[datetime.date(2026, 3, 23)]
    assert datetime.date(2026, 11, 2) in holidays  # Todos los Santos, moved from Nov 1 (Sun)
    assert "Todos los Santos" in holidays[datetime.date(2026, 11, 2)]
    print("test_confirmed_real_2026_movable_dates: PASS")


def test_new_2026_chiquinquira_holiday():
    """Ley 2578 de 2026: new 9 July holiday, observed 13 July 2026 via the
    Monday-shift rule. Independently confirmed via multiple news sources
    (Portafolio, Semana, Bloomberg Línea) and the law text itself."""
    holidays_2026 = CH.colombia_holidays_for_year(2026)
    assert datetime.date(2026, 7, 13) in holidays_2026
    assert "Chiquinquirá" in holidays_2026[datetime.date(2026, 7, 13)]

    holidays_2025 = CH.colombia_holidays_for_year(2025)
    assert not any("Chiquinquirá" in name for name in holidays_2025.values())
    print("test_new_2026_chiquinquira_holiday: PASS")


def test_2025_san_pedro_sagrado_corazon_collision():
    """A genuine calendar coincidence found while hand-verifying this
    module's previous implementation: in 2025, San Pedro y San Pablo
    (shifted from Sun 29 Jun) and Sagrado Corazón de Jesús both land on
    Mon 30 Jun. The library represents this as ONE date with both names
    combined, rather than silently losing one (as the old hand-rolled
    dict-based version did) - confirm that's still true."""
    holidays_2025 = CH.colombia_holidays_for_year(2025)
    combined = holidays_2025[datetime.date(2025, 6, 30)]
    assert "Sagrado Corazón" in combined
    assert "San Pedro" in combined
    assert len(holidays_2025) == 17  # 18 nominal festivos, minus this collision
    print("test_2025_san_pedro_sagrado_corazon_collision: PASS")


def test_semana_santa_relative_to_easter():
    holidays = CH.colombia_holidays_for_year(2026)
    easter = datetime.date(2026, 4, 5)
    jueves_santo = easter - datetime.timedelta(days=3)
    viernes_santo = easter - datetime.timedelta(days=2)
    assert "Jueves Santo" in holidays[jueves_santo]
    assert "Viernes Santo" in holidays[viernes_santo]
    assert jueves_santo.weekday() == 3  # Thursday
    assert viernes_santo.weekday() == 4  # Friday
    print("test_semana_santa_relative_to_easter: PASS")


def test_total_count_is_19_from_2026():
    holidays_2026 = CH.colombia_holidays_for_year(2026)
    assert len(holidays_2026) == 19, f"expected 19 festivos in 2026, got {len(holidays_2026)}: {sorted(holidays_2026)}"
    print("test_total_count_is_19_from_2026: PASS")


def test_holidays_in_range_spans_multiple_years():
    start = datetime.date(2025, 12, 20)
    end = datetime.date(2026, 1, 15)
    holidays = CH.colombia_holidays_in_range(start, end)
    assert datetime.date(2025, 12, 25) in holidays  # from 2025
    assert datetime.date(2026, 1, 1) in holidays    # from 2026
    assert datetime.date(2026, 1, 12) in holidays   # from 2026
    assert all(start <= d <= end for d in holidays)
    print("test_holidays_in_range_spans_multiple_years: PASS")


def test_gtfs_date_roundtrip():
    d = datetime.date(2026, 3, 15)
    assert CH.format_gtfs_date(d) == "20260315"
    assert CH.parse_gtfs_date("20260315") == d
    print("test_gtfs_date_roundtrip: PASS")


def test_colombian_holidays_alias_all_signatures():
    """reconstruct.py imports `colombian_holidays` (not the
    colombia_holidays_for_year/colombia_holidays_in_range names) - this
    alias must exist and work regardless of which of the plausible call
    conventions it's actually used with."""
    r0 = CH.colombian_holidays()
    assert datetime.date.today().year in {d.year for d in r0}

    r_year = CH.colombian_holidays(2026)
    assert datetime.date(2026, 1, 1) in r_year
    assert len(r_year) == 19

    r_date = CH.colombian_holidays(datetime.date(2026, 5, 1))
    assert r_date == r_year

    r_range = CH.colombian_holidays(datetime.date(2026, 1, 1), datetime.date(2026, 2, 1))
    assert datetime.date(2026, 1, 1) in r_range
    assert datetime.date(2026, 1, 12) in r_range
    assert datetime.date(2026, 3, 23) not in r_range  # outside the range

    try:
        CH.colombian_holidays(1, 2, 3)
        assert False, "expected TypeError for 3 args"
    except TypeError:
        pass
    print("test_colombian_holidays_alias_all_signatures: PASS")


def test_spanish_names_regardless_of_system_locale():
    """Real CI failure, reproduced: holidays==0.105 falls back to the
    runner's system locale (LANG/LC_ALL) instead of Colombia's own
    default_language='es' when the locale looks English - GitHub Actions'
    default locale silently produced English names ('Epiphany (observed)'
    instead of 'Día de los Reyes Magos (observado)'). language='es' must
    be passed explicitly in colombia_holidays_for_year/_in_range - this
    locks that in so it can't silently regress. Can't change this
    process's already-loaded locale mid-test, so this re-imports a fresh
    subprocess under an English-like locale instead."""
    import subprocess
    import sys
    code = (
        "import colombia_holidays as CH, datetime;"
        "h = CH.colombia_holidays_for_year(2026);"
        "name = h[datetime.date(2026, 1, 12)];"
        "assert 'Reyes' in name, f'got English fallback: {name!r}';"
        "print('OK')"
    )
    env = {"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "PATH": __import__("os").environ.get("PATH", "")}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert result.returncode == 0 and "OK" in result.stdout, \
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    print("test_spanish_names_regardless_of_system_locale: PASS")


if __name__ == "__main__":
    test_easter_2026()
    test_fixed_holidays_never_move()
    test_confirmed_real_2026_movable_dates()
    test_new_2026_chiquinquira_holiday()
    test_2025_san_pedro_sagrado_corazon_collision()
    test_semana_santa_relative_to_easter()
    test_total_count_is_19_from_2026()
    test_holidays_in_range_spans_multiple_years()
    test_gtfs_date_roundtrip()
    test_colombian_holidays_alias_all_signatures()
    test_spanish_names_regardless_of_system_locale()
    print("\nAll colombia_holidays.py tests passed.")
