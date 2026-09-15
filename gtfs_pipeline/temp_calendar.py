"""
temp_calendar.py
=================
Builds a TEMPORARY calendar.txt using the day-type convention documented
in the separate "rutas" dataset's HABIL / SABADO / DOM_FEST columns:

    HABIL    -> Monday-Friday service
    SABADO   -> Saturday service
    DOM_FEST -> Sunday AND Colombian national festivos service

This exists because the main GTFS FeatureServer's Calendars table
currently returns 0 rows (confirmed on a live run) - there is no real
weekly service pattern to build calendar.txt from at all right now, only
a handful of CalendarExceptions rows. This is a best-effort STAND-IN, not
a verified mapping to Metro Cali's real service_id values.

One thing worth testing directly: a live run showed exactly 4 distinct
CalendarID/service_id values in use across all of Runs. That's a
suggestive (not confirmed) match to the 4 canonical day-type codes this
dataset itself uses (HABIL, SABADO, DOM_FEST, SABADO_DOMINGO) - if Metro
Cali's real service_id values in CalendarExceptions.GServiceID literally
ARE these day-type strings, this temporary calendar directly reconciles
with the real trips.txt/calendar_dates.txt data rather than being purely
synthetic. cross_check_against_real_service_ids() checks this hypothesis
against whatever real service_id values are already downloaded, and
reports the result plainly rather than assuming it's true.

No network calls in here - see build_temp_calendar.py for the CLI wrapper.
"""
import pandas as pd

import colombia_holidays as CH

DAY_TYPE_WEEKDAY_FLAGS = {
    "HABIL": {"monday": 1, "tuesday": 1, "wednesday": 1, "thursday": 1,
              "friday": 1, "saturday": 0, "sunday": 0},
    "SABADO": {"monday": 0, "tuesday": 0, "wednesday": 0, "thursday": 0,
               "friday": 0, "saturday": 1, "sunday": 0},
    "DOM_FEST": {"monday": 0, "tuesday": 0, "wednesday": 0, "thursday": 0,
                 "friday": 0, "saturday": 0, "sunday": 1},
}

DAY_COLS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

DIAGNOSTICS = []


def diag(msg):
    DIAGNOSTICS.append(msg)
    print(f"  [diagnostic] {msg}")


def build_temporary_calendar(start_date, end_date, service_ids=("HABIL", "SABADO", "DOM_FEST")):
    """start_date/end_date: 'YYYYMMDD' strings. Returns a calendar.txt-shaped
    DataFrame with one row per requested day-type service_id."""
    rows = []
    for sid in service_ids:
        flags = DAY_TYPE_WEEKDAY_FLAGS.get(sid)
        if flags is None:
            raise ValueError(f"Unknown day-type service_id '{sid}' - expected "
                              f"one of {list(DAY_TYPE_WEEKDAY_FLAGS)}")
        row = {"service_id": sid}
        row.update({k: str(v) for k, v in flags.items()})
        row["start_date"] = start_date
        row["end_date"] = end_date
        rows.append(row)
    return pd.DataFrame(rows)


def cross_check_against_real_service_ids(real_service_ids, day_type_service_ids=("HABIL", "SABADO", "DOM_FEST")):
    """Compares the day-type service_id names against whatever real
    service_id values are already in use (e.g. from trips.txt or
    CalendarExceptions). Returns (matched, unmatched_real, unmatched_daytype)
    so a human can see whether the hypothesis - that Metro Cali's real
    service_id values literally ARE these day-type codes - actually holds,
    rather than assuming it."""
    real = set(real_service_ids)
    day_types = set(day_type_service_ids)
    matched = real & day_types
    unmatched_real = real - day_types
    unmatched_daytype = day_types - real
    return matched, unmatched_real, unmatched_daytype


def add_festivo_exceptions(calendar_df, calendar_dates_df=None):
    """Given a GTFS-shaped calendar.txt (service_id, monday..sunday,
    start_date, end_date) and an optional existing GTFS-shaped
    calendar_dates.txt (service_id, date, exception_type), returns a new
    calendar_dates_df with Colombian festivo add/suppress exceptions
    appended - the standard pattern for this feed's HABIL/SABADO/DOM_FEST
    convention:
        - a service that would normally run on the festivo's actual
          weekday (and isn't itself the DOM_FEST-style Sunday service)
          gets suppressed (exception_type=2)
        - the service that runs on Sundays gets added on the festivo
          (exception_type=1), unless the festivo is itself a Sunday
    Never overrides an existing (service_id, date) exception - explicit
    data always wins.
    """
    existing_pairs = set()
    base_rows = []
    if calendar_dates_df is not None and not calendar_dates_df.empty:
        base_rows = calendar_dates_df.to_dict("records")
        existing_pairs = set(zip(calendar_dates_df["service_id"], calendar_dates_df["date"]))

    new_rows = []
    n_suppressed = 0
    n_added = 0

    for _, row in calendar_df.iterrows():
        service_id = row["service_id"]
        try:
            start = CH.parse_gtfs_date(str(row["start_date"]))
            end = CH.parse_gtfs_date(str(row["end_date"]))
        except (ValueError, TypeError):
            diag(f"could not parse start_date/end_date for service_id '{service_id}' - skipped")
            continue
        if start > end:
            continue

        runs_sunday = str(row.get("sunday")) == "1"
        festivos = CH.colombia_holidays_in_range(start, end)

        for d, name in festivos.items():
            date_str = CH.format_gtfs_date(d)
            if (service_id, date_str) in existing_pairs:
                continue
            weekday_col = DAY_COLS[d.weekday()]

            if weekday_col != "sunday" and str(row.get(weekday_col)) == "1":
                new_rows.append({"service_id": service_id, "date": date_str, "exception_type": "2"})
                existing_pairs.add((service_id, date_str))
                n_suppressed += 1
            elif weekday_col != "sunday" and runs_sunday:
                new_rows.append({"service_id": service_id, "date": date_str, "exception_type": "1"})
                existing_pairs.add((service_id, date_str))
                n_added += 1

    diag(f"festivo exceptions: {n_suppressed} suppressed (exception_type=2), "
         f"{n_added} added (exception_type=1)")

    all_rows = base_rows + new_rows
    if not all_rows:
        return None
    return pd.DataFrame(all_rows, columns=["service_id", "date", "exception_type"]) \
        .drop_duplicates(subset=["service_id", "date"], keep="first") \
        .sort_values(["date", "service_id"]).reset_index(drop=True)
