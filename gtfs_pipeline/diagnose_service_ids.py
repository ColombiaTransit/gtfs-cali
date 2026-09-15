"""
diagnose_service_ids.py
=========================
The HABIL/SABADO/DOM_FEST hypothesis (see temp_calendar.py) was tested
against real data and refuted: the feed's actual service_id values
(e.g. "8099041") don't match that naming convention at all. This script
digs into what evidence we DO have about each real service_id, to help
figure out what they actually mean before attempting any calendar
reconstruction:

  1. How many trips and distinct routes use each service_id - a service
     used by very few trips is a plausible candidate for a low-frequency
     pattern (Sunday/holiday); one used by most trips is a plausible
     candidate for the everyday pattern.
  2. Every date each service_id already has an explicit calendar_dates.txt
     exception for (from Metro Cali's real CalendarExceptions), with the
     weekday and exception_type - if a service_id's exceptions cluster on
     a particular weekday or day-type, that's real signal about what it
     represents.

This is diagnostic only - it does not guess or assign day-type labels.
It prints/reports what the data actually shows so a human (ideally with
input from Metro Cali) can make the call.

No network calls - operates on already-downloaded trips.txt and
calendar_dates.txt (either from build/gtfs_raw/ after download.py, or
extracted from the committed data/gtfs.zip - see the CLI wrapper below).
"""
import datetime

import pandas as pd

import colombia_holidays as CH


def summarize_service_ids(trips_df, calendar_dates_df=None):
    """Returns a DataFrame, one row per distinct service_id, with trip/route
    counts and exception-date statistics. Does not label or guess anything."""
    if trips_df is None or trips_df.empty:
        return pd.DataFrame()

    trip_counts = trips_df.groupby("service_id").size().rename("n_trips")
    route_counts = trips_df.groupby("service_id")["route_id"].nunique().rename("n_routes")
    summary = pd.concat([trip_counts, route_counts], axis=1).reset_index()

    exception_weekdays = {}
    exception_dates_list = {}
    is_festivo_list = {}
    date_range = {}
    if calendar_dates_df is not None and not calendar_dates_df.empty:
        for sid, group in calendar_dates_df.groupby("service_id"):
            weekdays = []
            dates = []
            is_festivo = []
            for _, row in group.iterrows():
                try:
                    d = CH.parse_gtfs_date(str(row["date"]))
                except (ValueError, TypeError):
                    continue
                weekdays.append(d.strftime("%A"))
                dates.append((row["date"], row.get("exception_type")))
                is_festivo.append(d in CH.colombia_holidays_for_year(d.year))
            exception_weekdays[sid] = weekdays
            exception_dates_list[sid] = dates
            is_festivo_list[sid] = is_festivo
            if dates:
                raw_dates = [dt for dt, _ in dates]
                date_range[sid] = (min(raw_dates), max(raw_dates))

    summary["n_exceptions"] = summary["service_id"].map(lambda s: len(exception_weekdays.get(s, [])))
    summary["exception_weekdays"] = summary["service_id"].map(lambda s: exception_weekdays.get(s, []))
    summary["exception_dates"] = summary["service_id"].map(lambda s: exception_dates_list.get(s, []))
    summary["n_exceptions_on_festivo"] = summary["service_id"].map(
        lambda s: sum(is_festivo_list.get(s, []))
    )
    summary["exception_date_range"] = summary["service_id"].map(lambda s: date_range.get(s))
    return summary.sort_values("n_trips", ascending=False).reset_index(drop=True)


def format_report(summary_df):
    if summary_df.empty:
        return "No trips.txt data available to summarize."

    lines = ["=== Real service_id diagnostic ===", ""]

    # Overall staleness check across all exception dates combined, not just
    # per service_id - a single glance at whether this data describes the
    # expected period at all. (Seen in practice: a committed feed's
    # calendar_dates.txt turned out to be entirely 2025 dates.)
    all_years = set()
    for r in summary_df.get("exception_date_range", []):
        if r:
            all_years.add(int(r[0][:4]))
            all_years.add(int(r[1][:4]))
    if all_years:
        current_year = datetime.date.today().year
        if all(y < current_year for y in all_years):
            lines.append(f"NOTE: all exception dates found are from {sorted(all_years)} - "
                          f"none from {current_year} or later. This calendar_dates.txt data "
                          f"looks STALE (a previous year/vigencia), not current.")
            lines.append("")

    total_trips = summary_df["n_trips"].sum()
    for _, row in summary_df.iterrows():
        pct = 100 * row["n_trips"] / total_trips if total_trips else 0
        lines.append(f"service_id = {row['service_id']}")
        lines.append(f"  trips: {row['n_trips']} ({pct:.1f}% of all trips), "
                      f"distinct routes: {row['n_routes']}")
        if row["n_exceptions"]:
            wd_counts = pd.Series(row["exception_weekdays"]).value_counts().to_dict()
            date_range = row.get("exception_date_range")
            range_str = f", date range: {date_range[0]}-{date_range[1]}" if date_range else ""
            lines.append(f"  calendar_dates.txt exceptions: {row['n_exceptions']} total{range_str}, "
                         f"by weekday: {wd_counts}")
            lines.append(f"  of those, {row['n_exceptions_on_festivo']} land on a known "
                         f"Colombian festivo date")
            for date_str, exc_type in row["exception_dates"]:
                lines.append(f"    {date_str} (exception_type={exc_type})")
        else:
            lines.append("  no existing calendar_dates.txt exceptions for this service_id "
                         "- no direct date evidence available")
        lines.append("")

    lines.append("Read this as evidence, not a conclusion: a service_id used by ~1/7th of "
                  "trips with exceptions clustering on Sundays is a plausible Sunday-type "
                  "service; one used by most trips with few/no exceptions is plausible as "
                  "the everyday pattern. But this script does not assign labels - that call "
                  "needs a human (ideally confirmed with Metro Cali, sistemas@metrocali.gov.co).")
    return "\n".join(lines)
