"""
Colombian national public holidays.

Returns the observed public-holiday dates for Colombia.

The rules are based on Ley 51 de 1983 ("Ley Emiliani"), which moves
specified holidays to Monday, plus the fixed holidays and Easter-related
holidays.

July 9 became a new national holiday through Ley 2578 de 2026. When July 9
is not a Monday, the observed holiday is the following Monday.

This module deliberately contains no pandas or GTFS-specific logic so it
can be tested independently.
"""

from datetime import date, timedelta


# Holidays that are transferred to Monday under Ley 51 de 1983.
# The date in this dictionary is the legal/original celebration date.
EMILIANI_HOLIDAYS = {
    (1, 6): "Día de los Reyes Magos",
    (3, 19): "Día de San José",
    (6, 29): "San Pedro y San Pablo",
    (8, 15): "Asunción de la Virgen",
    (10, 12): "Día de la Raza",
    (11, 1): "Todos los Santos",
    (11, 11): "Independencia de Cartagena",
}


# Holidays that remain on their calendar date.
FIXED_HOLIDAYS = {
    (1, 1): "Año Nuevo",
    (5, 1): "Día del Trabajo",
    (7, 20): "Día de la Independencia",
    (8, 7): "Batalla de Boyacá",
    (12, 8): "Inmaculada Concepción",
    (12, 25): "Navidad",
}


def _next_monday(day: date) -> date:
    """Return day if Monday, otherwise the following Monday."""
    return day + timedelta(days=(7 - day.weekday()) % 7)


def _easter_sunday(year: int) -> date:
    """
    Calculate Gregorian Easter Sunday.

    Uses the Anonymous Gregorian algorithm, so no external dependency
    is required.
    """
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

    return date(year, month, day)


def colombian_holidays(year: int) -> dict[date, str]:
    """
    Return Colombian national public holidays for `year`.

    The dictionary keys are the dates on which the public holiday is
    actually observed.

    Examples for 2026:
        January 6  -> January 12
        March 19   -> March 23
        July 9     -> July 13
        October 12 -> October 12
    """
    holidays = {}

    # Fixed-date holidays.
    for (month, day), name in FIXED_HOLIDAYS.items():
        holidays[date(year, month, day)] = name

    # Ley Emiliani holidays.
    for (month, day), name in EMILIANI_HOLIDAYS.items():
        original_date = date(year, month, day)
        observed_date = _next_monday(original_date)
        holidays[observed_date] = name

    # Easter-relative holidays.
    easter = _easter_sunday(year)

    # These remain on their actual dates.
    holidays[easter - timedelta(days=3)] = "Jueves Santo"
    holidays[easter - timedelta(days=2)] = "Viernes Santo"

    # These are transferred to Monday.
    ascension = easter + timedelta(days=39)
    corpus_christi = easter + timedelta(days=60)
    sacred_heart = easter + timedelta(days=68)

    holidays[_next_monday(ascension)] = "Ascensión del Señor"
    holidays[_next_monday(corpus_christi)] = "Corpus Christi"
    holidays[_next_monday(sacred_heart)] = "Sagrado Corazón de Jesús"

    # New national holiday created by Ley 2578 de 2026.
    #
    # The holiday is July 9. As with the applicable Ley Emiliani
    # holidays, the observed day is Monday when July 9 is not itself
    # a Monday.
    if year >= 2026:
        chiquinquira = date(year, 7, 9)
        holidays[_next_monday(chiquinquira)] = (
            "Día de Nuestra Señora del Rosario de Chiquinquirá"
        )

    return dict(sorted(holidays.items()))
