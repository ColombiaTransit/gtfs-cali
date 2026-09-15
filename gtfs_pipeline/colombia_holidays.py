"""
colombia_holidays.py
=====================
Computes Colombian public holidays (festivos) for any year, following
Ley Emiliani (Law 51 of 1983): most religious/civic holidays that don't
fall on a Monday are moved to the following Monday, to create long
weekends ("puentes festivos"). A handful of holidays are fixed and never
move. No network calls - pure calendar arithmetic, deterministic by law.

--- The 18 official festivos --------------------------------------------
Fixed, never moved:
    Jan 1   Año Nuevo
    May 1   Día del Trabajo
    Jul 20  Día de la Independencia
    Aug 7   Batalla de Boyacá
    Dec 8   Inmaculada Concepción
    Dec 25  Navidad

Fixed calendar date, moved to the next Monday if not already a Monday:
    Jan 6   Reyes Magos
    Mar 19  San José
    Jun 29  San Pedro y San Pablo
    Aug 15  Asunción de la Virgen
    Oct 12  Día de la Raza
    Nov 1   Todos los Santos
    Nov 11  Independencia de Cartagena

Relative to Easter Sunday, NEVER moved (already fall on Thu/Fri):
    Easter - 3  Jueves Santo
    Easter - 2  Viernes Santo

Relative to Easter Sunday, then moved to the next Monday:
    Easter + 39  Ascensión del Señor  (Thursday before the move)
    Easter + 60  Corpus Christi       (Thursday before the move)
    Easter + 68  Sagrado Corazón de Jesús (Friday before the move)

Easter Sunday is computed with the Anonymous Gregorian algorithm
(Meeus/Jones/Butcher), accurate for the Gregorian calendar (1583+).
"""
import datetime

FIXED_NO_MOVE = [
    (1, 1, "Año Nuevo"),
    (5, 1, "Día del Trabajo"),
    (7, 20, "Día de la Independencia"),
    (8, 7, "Batalla de Boyacá"),
    (12, 8, "Inmaculada Concepción"),
    (12, 25, "Navidad"),
]

MOVABLE_TO_MONDAY_FIXED_DATE = [
    (1, 6, "Reyes Magos"),
    (3, 19, "San José"),
    (6, 29, "San Pedro y San Pablo"),
    (8, 15, "Asunción de la Virgen"),
    (10, 12, "Día de la Raza"),
    (11, 1, "Todos los Santos"),
    (11, 11, "Independencia de Cartagena"),
]

EASTER_RELATIVE_NO_MOVE = [
    (-3, "Jueves Santo"),
    (-2, "Viernes Santo"),
]

EASTER_RELATIVE_MOVABLE = [
    (39, "Ascensión del Señor"),
    (60, "Corpus Christi"),
    (68, "Sagrado Corazón de Jesús"),
]

# Ley 2578 de 2026 (sanctioned 1 June 2026): a new national holiday, Día de
# Nuestra Señora del Rosario de Chiquinquirá, observed 9 July each year,
# moved to the next Monday like the Ley Emiliani holidays above (article 6
# of the law explicitly applies Ley 51 de 1983's rules). Confirmed via
# multiple independent sources (Portafolio, Semana, Bloomberg Línea,
# Noticias Caracol, and the law text itself) - not something known at this
# module's original writing, added to match the version already committed
# upstream. Only applies from 2026 onward.
NEW_HOLIDAY_MIN_YEAR = 2026
NEW_HOLIDAY_MOVABLE_FIXED_DATE = (7, 9, "Día de Nuestra Señora del Rosario de Chiquinquirá")


def easter_sunday(year):
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher). Returns a
    datetime.date for Easter Sunday in the given (Gregorian) year."""
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


def next_monday_on_or_after(d):
    """If d is already a Monday, return it unchanged; otherwise return the
    following Monday."""
    days_ahead = (7 - d.weekday()) % 7  # weekday(): Monday=0 ... Sunday=6
    return d if days_ahead == 0 else d + datetime.timedelta(days=days_ahead)


def colombia_holidays_for_year(year):
    """Returns {date: name} for all festivos in the given year (18 before
    2026, 19 from 2026 onward - see NEW_HOLIDAY_MIN_YEAR)."""
    holidays = {}
    for month, day, name in FIXED_NO_MOVE:
        holidays[datetime.date(year, month, day)] = name
    for month, day, name in MOVABLE_TO_MONDAY_FIXED_DATE:
        holidays[next_monday_on_or_after(datetime.date(year, month, day))] = name

    easter = easter_sunday(year)
    for offset, name in EASTER_RELATIVE_NO_MOVE:
        holidays[easter + datetime.timedelta(days=offset)] = name
    for offset, name in EASTER_RELATIVE_MOVABLE:
        base = easter + datetime.timedelta(days=offset)
        holidays[next_monday_on_or_after(base)] = name

    if year >= NEW_HOLIDAY_MIN_YEAR:
        month, day, name = NEW_HOLIDAY_MOVABLE_FIXED_DATE
        holidays[next_monday_on_or_after(datetime.date(year, month, day))] = name

    return holidays


def colombia_holidays_in_range(start_date, end_date):
    """Returns {date: name} for every festivo whose date falls within
    [start_date, end_date] inclusive, spanning as many years as needed.
    start_date/end_date are datetime.date."""
    holidays = {}
    for year in range(start_date.year, end_date.year + 1):
        for d, name in colombia_holidays_for_year(year).items():
            if start_date <= d <= end_date:
                holidays[d] = name
    return holidays


def parse_gtfs_date(s):
    """'20260315' -> datetime.date(2026, 3, 15)."""
    return datetime.datetime.strptime(s, "%Y%m%d").date()


def format_gtfs_date(d):
    """datetime.date(2026, 3, 15) -> '20260315'."""
    return d.strftime("%Y%m%d")
