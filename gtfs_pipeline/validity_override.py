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
# Inferred from the schedule diagnostic (live run of 2026-11-17, all 94 routes decoded):
# 41 = weekday (A11 05:00-23:18, E37 until 20:36, A14A present), 42 = Saturday (A11 until 14:57,
# E37 until 14:38, A14B present), 43 = Sunday (A14B 06:00-22:17); 43 and 148 are near-identical
# twins (76 routes each = the 76 routes that run on Sundays), so which of the two is "Sunday"
# and which "festivo" cannot be told: 43 = Sunday, 148 = festivo only (calendar_dates only).
SERVICE_DAYTYPE = {"8099041": "HABIL", "8099042": "SABADO",
                   "8099043": "DOM_FEST", "8099148": "FESTIVO"}
DAYTYPE_FLAGS = {"HABIL": (1, 1, 1, 1, 1, 0, 0), "SABADO": (0, 0, 0, 0, 0, 1, 0),
                 "DOM_FEST": (0, 0, 0, 0, 0, 0, 1),
                 "FESTIVO": (0, 0, 0, 0, 0, 0, 0)}  # runs only on festivos, via calendar_dates


def override_days():
    try:
        return int(os.environ.get("VALIDITY_OVERRIDE_DAYS", DEFAULT_DAYS))
    except ValueError:
        return DEFAULT_DAYS


def window(today=None, days=None):
    today = today or date.today()
    days = DEFAULT_DAYS if days is None else days
    return today.strftime("%Y%m%d"), (today + timedelta(days=days)).strftime("%Y%m%d")


def holiday_exceptions(calendar, festivos, service_daytype=None, service_ids=None):
    """calendar_dates rows for the festivos.

    On a festivo that falls Monday-Saturday the regular services running that weekday are
    removed (type 2) and every FESTIVO service is added (type 1). A festivo on a Sunday
    changes nothing: the Sunday service already runs and the FESTIVO twin stays off,
    so the same trips are not offered twice.
    """
    service_daytype = SERVICE_DAYTYPE if service_daytype is None else service_daytype
    festivo_ids = {s for s, t in service_daytype.items() if t == "FESTIVO"}
    if service_ids is not None:
        festivo_ids &= {str(x) for x in service_ids}
    rows = []
    for d in sorted(festivos):
        wd = d.weekday()
        if wd == 6:
            continue
        ds = d.strftime("%Y%m%d")
        for sid in sorted(festivo_ids):
            rows.append({"service_id": sid, "date": ds, "exception_type": "1"})
        for _, r in calendar.iterrows():
            if r["service_id"] not in festivo_ids and str(r[DAYS[wd]]) == "1":
                rows.append({"service_id": r["service_id"], "date": ds, "exception_type": "2"})
    return pd.DataFrame(rows, columns=["service_id", "date", "exception_type"])


def apply_validity_override(calendar, calendar_dates, service_ids, today=None, days=None,
                            log=print, festivos=None):
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

    # A festivo-only service is defined purely through calendar_dates.txt (type 1 on each
    # festivo). A calendar.txt row with all weekday flags 0 would only trigger the validator's
    # service_has_no_active_day_of_the_week warning. Needs the festivo dates, else keep the row.
    if festivos:
        festivo_only = [m for m in missing if SERVICE_DAYTYPE.get(str(m)) == "FESTIVO"]
        if festivo_only:
            log(f"calendar.txt: {festivo_only} defined in calendar_dates.txt only (festivo service)")
            missing = [m for m in missing if m not in festivo_only]

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
            msg = (f"{len(unmapped)} service_id(s) have no confirmed day type: {unmapped}. Metro Cali probably "
                   f"issued new service ids; check build/report/service_id_mapping_proposed.json and fill in "
                   f"validity_override.SERVICE_DAYTYPE.")
            if os.environ.get("VALIDITY_ALLOW_UNMAPPED") == "1":
                log(f"WARNING: {msg} They run EVERY DAY (VALIDITY_ALLOW_UNMAPPED=1).")
            else:
                raise ValueError(msg + " Set VALIDITY_ALLOW_UNMAPPED=1 to publish them as every-day services anyway.")

    if calendar_dates is not None and len(calendar_dates):
        keep = (calendar_dates["date"] >= start) & (calendar_dates["date"] <= end)
        dropped = int((~keep).sum())
        if dropped:
            log(f"calendar_dates.txt: dropped {dropped} row(s) outside {start}-{end}")
        calendar_dates = calendar_dates[keep].reset_index(drop=True)
        if calendar_dates.empty:
            calendar_dates = None
    if festivos and any(t == "FESTIVO" for t in SERVICE_DAYTYPE.values()):
        gen = holiday_exceptions(calendar if calendar is not None else pd.DataFrame(columns=["service_id"] + DAYS),
                                 festivos, service_ids=service_ids)
        if len(gen):
            calendar_dates = gen if calendar_dates is None else pd.concat(
                [gen, calendar_dates], ignore_index=True).drop_duplicates(["service_id", "date"])
            log(f"calendar_dates.txt: {len(gen)} festivo exception row(s) for {len(set(gen['date']))} festivo(s)")
    return calendar, calendar_dates, start, end


def override_feed_info(feed_info, start, end):
    feed_info = feed_info.copy()
    feed_info["feed_start_date"], feed_info["feed_end_date"] = start, end
    return feed_info
