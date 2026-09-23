"""
colombia_holidays.py
=====================
Computes Colombian public holidays (festivos) using the actively
maintained `holidays` PyPI package (https://pypi.org/project/holidays/),
which implements Ley Emiliani (Law 51 of 1983 - most holidays not falling
on a Monday move to the following Monday) for Colombia and is kept
current with new legislation.

Confirmed (2026-09) to already include Ley 2578 de 2026 - the brand-new
9 July "Día de Nuestra Señora del Rosario de Chiquinquirá" holiday,
observed 13 July 2026 via the Monday-shift rule - matching independent
verification against real news coverage of the 2026 calendar. Also
correctly reproduces a genuine edge case found by hand-verifying this
module's previous custom implementation: in 2025, San Pedro y San Pablo
(shifted from Sun 29 Jun) and Sagrado Corazón de Jesús both land on the
same Monday (30 Jun) - the library represents this as one date with a
combined name rather than losing one holiday, which is better than the
naive dict-overwrite this module used to do.

This module previously computed festivos from scratch (fixed dates, the
Monday-shift rule, Easter-relative offsets, hardcoded per Colombian
holiday law). That hand-rolled logic has been replaced by this thin
wrapper - one less thing to keep correct by hand as holiday law changes
(e.g. Ley 2578 de 2026 was sanctioned mid-year and would otherwise have
required a manual code update, as happened once already here).
"""
import datetime

import holidays as _holidays_lib


def easter_sunday(year):
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher). Standalone,
    independent of the holidays package - kept as a quick correctness
    sanity-check (see test_easter_2026), not used for festivo computation
    itself anymore."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime.date(year, month, day)


def colombia_holidays_for_year(year):
    """Returns {date: name} for all festivos in the given year."""
    return dict(_holidays_lib.Colombia(years=year))


def colombia_holidays_in_range(start_date, end_date):
    """Returns {date: name} for every festivo whose date falls within
    [start_date, end_date] inclusive, spanning as many years as needed.
    start_date/end_date are datetime.date."""
    years = list(range(start_date.year, end_date.year + 1))
    co = _holidays_lib.Colombia(years=years)
    return {d: name for d, name in co.items() if start_date <= d <= end_date}


def colombian_holidays(*args):
    """Alias matching reconstruct.py's `from colombia_holidays import
    colombian_holidays` import - that exact name doesn't otherwise exist
    in this module. Flexible on call signature since the exact call site
    wasn't available to confirm against:
        colombian_holidays()                      -> current year
        colombian_holidays(2026)                   -> colombia_holidays_for_year(2026)
        colombian_holidays(some_date)               -> colombia_holidays_for_year(some_date.year)
        colombian_holidays(start_date, end_date)    -> colombia_holidays_in_range(...)
    """
    if len(args) == 0:
        return colombia_holidays_for_year(datetime.date.today().year)
    if len(args) == 1:
        year = args[0].year if isinstance(args[0], datetime.date) else int(args[0])
        return colombia_holidays_for_year(year)
    if len(args) == 2:
        return colombia_holidays_in_range(args[0], args[1])
    raise TypeError(f"colombian_holidays() expects 0-2 positional arguments, got {len(args)}")


def parse_gtfs_date(s):
    """'20260315' -> datetime.date(2026, 3, 15)."""
    return datetime.datetime.strptime(s, "%Y%m%d").date()


def format_gtfs_date(d):
    """datetime.date(2026, 3, 15) -> '20260315'."""
    return d.strftime("%Y%m%d")
