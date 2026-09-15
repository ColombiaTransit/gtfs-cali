"""
Step 2.5 (optional/manual) - Build a temporary calendar.txt + calendar_dates.txt
===================================================================================
Metro Cali's own Calendars table currently returns 0 rows (confirmed live)
- there is no real weekly service pattern to build calendar.txt from, only
a handful of CalendarExceptions rows. This synthesizes a stand-in
calendar.txt using the day-type convention documented in the separate
"rutas" dataset (HABIL=weekday, SABADO=Saturday, DOM_FEST=Sunday+festivos),
then generates calendar_dates.txt festivo exceptions against it.

IMPORTANT - this is a best-effort stand-in, not verified real Metro Cali
data. It assumes trips.txt's real service_id values are literally "HABIL",
"SABADO", "DOM_FEST". This script CROSS-CHECKS that assumption against
whatever real service_id values are already in trips.txt (if it's been
downloaded) and reports the result plainly - it does not silently assume
the hypothesis is true, and does not relabel your trips.

Run between download.py and fix.py if you want to try this:

    python download.py
    python build_temp_calendar.py   # <- this script
    python enrich_stops.py
    python enrich_routes.py
    python fix.py
    python validate.py

This OVERWRITES build/gtfs_raw/calendar.txt and calendar_dates.txt. The
existing calendar_dates.txt (Metro Cali's real CalendarExceptions, already
in GTFS shape at this point in the pipeline) is checked against the
requested date range: if it's all inside the range, it stays authoritative
and festivo exceptions only fill gaps. If it's ALL outside the range (seen
in practice: a committed feed's calendar_dates.txt turned out to be from
2025 while building a calendar for 2026), that's flagged loudly as likely
stale rather than silently trusted - pass --exclude-stale to drop it from
the output instead of just warning.
"""
import argparse

import pandas as pd

import temp_calendar as TC
from gtfs_common import RAW_DIR, ensure_dirs

DEFAULT_START = "20260101"
DEFAULT_END = "20261231"


def main(start_date=DEFAULT_START, end_date=DEFAULT_END, exclude_stale=False):
    ensure_dirs()
    calendar_df = TC.build_temporary_calendar(start_date, end_date)
    print(f"Built temporary calendar.txt ({start_date} - {end_date}):\n")
    print(calendar_df.to_string(index=False))

    try:
        existing_calendar_dates = pd.read_csv(
            f"{RAW_DIR}/calendar_dates.txt", dtype=str, keep_default_na=False, na_values=[""]
        )
        print(f"\nFound existing calendar_dates.txt ({len(existing_calendar_dates)} row(s)) - "
              f"kept authoritative, festivo exceptions only fill gaps. (See below for a "
              f"staleness check against the requested {start_date}-{end_date} range.)")
    except FileNotFoundError:
        print("\nNo existing calendar_dates.txt found - starting from an empty exceptions set.")
        existing_calendar_dates = None

    print()
    calendar_dates_df = TC.add_festivo_exceptions(
        calendar_df, existing_calendar_dates, exclude_stale_existing=exclude_stale
    )

    try:
        trips_df = pd.read_csv(f"{RAW_DIR}/trips.txt", dtype=str, keep_default_na=False, na_values=[""])
        real_ids = set(trips_df["service_id"].dropna())
        matched, unmatched_real, unmatched_dt = TC.cross_check_against_real_service_ids(real_ids)
        print(f"\n=== Cross-check against real trips.txt service_id values ===")
        print(f"  real service_id values found: {sorted(real_ids)}")
        print(f"  matched (real service_id IS a day-type code): {matched or '(none)'}")
        if unmatched_real:
            print(f"  WARNING: {len(unmatched_real)} real service_id value(s) do NOT match "
                  f"HABIL/SABADO/DOM_FEST: {sorted(unmatched_real)}")
            print(f"  Trips using those service_id values will have NO calendar.txt coverage "
                  f"from this temporary calendar until reconciled - either the day-type "
                  f"hypothesis is wrong for this feed, or these need manual remapping.")
        else:
            print(f"  All real service_id values matched the day-type convention - "
                  f"this temporary calendar directly covers your actual trips.txt.")
    except FileNotFoundError:
        print("\nNo trips.txt found yet to cross-check against (run download.py first "
              "for a meaningful cross-check).")

    calendar_df.to_csv(f"{RAW_DIR}/calendar.txt", index=False, encoding="utf-8")
    if calendar_dates_df is not None:
        calendar_dates_df.to_csv(f"{RAW_DIR}/calendar_dates.txt", index=False, encoding="utf-8")
        print(f"\nWrote {RAW_DIR}/calendar_dates.txt ({len(calendar_dates_df)} row(s))")
    print(f"Wrote {RAW_DIR}/calendar.txt ({len(calendar_df)} row(s))")
    print("\nNext: run enrich_stops.py / enrich_routes.py / fix.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START, help="YYYYMMDD, default 2026-01-01")
    parser.add_argument("--end", default=DEFAULT_END, help="YYYYMMDD, default 2026-12-31")
    parser.add_argument("--exclude-stale", action="store_true",
                         help="Drop existing calendar_dates.txt rows entirely if ALL of them "
                              "fall outside the --start/--end range (e.g. leftover exceptions "
                              "from a previous year's vigencia). Default: keep them, just warn.")
    args = parser.parse_args()
    main(args.start, args.end, exclude_stale=args.exclude_stale)
