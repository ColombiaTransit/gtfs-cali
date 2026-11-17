"""
Validity override
=================
The source feed has no usable validity window (Calendars is empty on the
server, CalendarExceptions is stale 2025 data), so the MobilityData validator
reports expired_calendar / feed_expiration_date7_days /
trip_coverage_not_active_for_next7_days.

This module forces the whole feed to be valid from today for N days
(default 183 = ~6 months):

  * calendar.txt        : every service_id gets start_date/end_date = window.
                          Weekday flags are kept if a real calendar exists.
                          If there is NO calendar, one is synthesized from
                          SERVICE_WEEKDAYS (default: every day, flagged loudly -
                          the real service_id -> weekday mapping is unconfirmed).
  * calendar_dates.txt  : rows outside the window are dropped (stale 2025 rows).
  * feed_info.txt       : feed_start_date / feed_end_date = window.

Set env VALIDITY_OVERRIDE_DAYS=0 to disable.
"""
import os
from datetime import date, datetime, timedelta

import pandas as pd

DEFAULT_DAYS = 183
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
# service_id -> 7 flags (Mon..Sun). Fill in once the mapping is confirmed, e.g.
# {"8099041": (1,1,1,1,1,0,0)}. Unlisted ids use DEFAULT_WEEKDAYS.
SERVICE_WEEKDAYS = {}
DEFAULT_WEEKDAYS = (1, 1, 1, 1, 1, 1, 1)
# Confirmed day type per real service_id, from diagnose_service_daytypes.py, e.g.
# {"8099041": "HABIL", "8099042": "SABADO", "8099043": "DOM_FEST"}
SERVICE_DAYTYPE = {}
DAYTYPE_FLAGS = {"HABIL": (1, 1, 1, 1, 1, 0, 0), "SABADO": (0, 0, 0, 0, 0, 1, 0),
                 "DOM_FEST": (0, 0, 0, 0, 0, 0, 1)}


def override_days():
    try:
        return int(os.environ.get("VALIDITY_OVERRIDE_DAYS", DEFAULT_DAYS))
    except ValueError:
        return DEFAULT_DAYS


def window(today=None, days=None):
    today = today or date.today()
    days = DEFAULT_DAYS if days is None else days
    return today.strftime("%Y%m%d"), (today + timedelta(days=days)).strftime("%Y%m%d")


def apply_validity_override(calendar, calendar_dates, service_ids, today=None, days=None, log=print):
    """Returns (calendar, calendar_dates, start, end)."""
    start, end = window(today, days)

    if calendar is not None and len(calendar):
        calendar = calendar.copy()
        calendar["start_date"], calendar["end_date"] = start, end
        have = set(calendar["service_id"])
        missing = sorted(set(service_ids) - have)
        log(f"calendar.txt: dates of {len(calendar)} service_id(s) overridden to {start}-{end}")
    else:
        calendar, missing = None, sorted(service_ids)

    if missing:
        rows = []
        for sid in missing:
            flags = SERVICE_WEEKDAYS.get(str(sid)) or DAYTYPE_FLAGS.get(SERVICE_DAYTYPE.get(str(sid)), DEFAULT_WEEKDAYS)
            row = {"service_id": sid}
            row.update({d: str(int(f)) for d, f in zip(DAYS, flags)})
            row.update({"start_date": start, "end_date": end})
            rows.append(row)
        new = pd.DataFrame(rows)
        calendar = new if calendar is None else pd.concat([calendar, new], ignore_index=True)
        unmapped = [s for s in missing if str(s) not in SERVICE_WEEKDAYS and str(s) not in SERVICE_DAYTYPE]
        log(f"calendar.txt: synthesized {len(rows)} service_id(s) for {start}-{end}")
        if unmapped:
            log(f"WARNING: {len(unmapped)} service_id(s) have no confirmed weekday pattern and run "
                f"EVERY DAY: {unmapped}. Fill in validity_override.SERVICE_DAYTYPE.")

    if calendar_dates is not None and len(calendar_dates):
        keep = (calendar_dates["date"] >= start) & (calendar_dates["date"] <= end)
        dropped = int((~keep).sum())
        if dropped:
            log(f"calendar_dates.txt: dropped {dropped} row(s) outside {start}-{end}")
        calendar_dates = calendar_dates[keep].reset_index(drop=True)
        if calendar_dates.empty:
            calendar_dates = None
    return calendar, calendar_dates, start, end


def override_feed_info(feed_info, start, end):
    feed_info = feed_info.copy()
    feed_info["feed_start_date"], feed_info["feed_end_date"] = start, end
    return feed_info
