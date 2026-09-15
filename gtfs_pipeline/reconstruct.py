"""
reconstruct.py
===============
Pure logic that turns the 9 raw layers/tables from the Metro Cali
FeatureServer (a relational "pattern + timing-offset + calendar" vehicle
schedule model, NOT flat GTFS tables) into standard GTFS DataFrames.

No network calls in here on purpose, so this can be unit-tested with
fabricated data (see test_reconstruct.py) independent of ArcGIS access.

--- The source schema, and how it maps to GTFS -----------------------------

Stops (points)              -> stops.txt (no stop_name in source!)
LineVariantElements (lines) -> shapes.txt + defines stop sequence per pattern
Calendars                   -> calendar.txt
CalendarExceptions          -> calendar_dates.txt
Lines                       -> routes.txt (no route names in source!)
LineVariants                -> links Lines to a direction + shape + pattern
Runs                        -> trips.txt (one row per actual scheduled trip)
Schedules                   -> links a Run to a stop-timing pattern (LineVariant)
ScheduleElements            -> stop_times.txt, as OFFSETS from Runs.StartRun

Join chain to build one trip's stop_times:
    Runs.ScheduleID -> Schedules.ID -> Schedules.LineVarID -> LineVariants.ID
        -> LineVariants.LineID -> Lines.ID            (route_id, route_type)
        -> LineVariants.GDirectionID, GShapeID         (direction_id, shape_id)
    LineVariantElements.LineVarID == LineVariants.ID, ordered by SqIdx
        -> defines the ordered stop sequence (FromStopID / ToStopID chain)
    ScheduleElements.ScheduleID == Schedules.ID, ordered by SqIdx
        -> Arrival / Departure offsets, ALIGNED to the stop sequence above
           (two possible alignments - see detect_schedule_alignment)
    absolute time = Runs.StartRun + offset, all in the same unit
        (unit is auto-detected - see detect_time_unit)
    Runs.CalendarID -> Calendars.ID -> Calendars.GServiceID  (trips.service_id)

Known source data gaps (not something this code can invent):
    - No Agency table at all            -> a fixed KNOWN_AGENCY record is used
    - Stops have no stop_name           -> placeholder "Parada {stop_id}"
    - Lines have no route name fields   -> placeholder route_short_name = route_id
  Both placeholders are flagged loudly in the fix report; get real names from
  Metro Cali (sistemas@metrocali.gov.co) before treating this as final.
"""
import math
from collections import Counter

import pandas as pd

from colombia_holidays import colombian_holidays

from gtfs_common import (
    KNOWN_AGENCY, SCHEDULE_ALIGNMENT_OVERRIDE, TIME_UNIT_OVERRIDE,
)

DIAGNOSTICS = []


def diag(msg):
    DIAGNOSTICS.append(msg)
    print(f"  [diagnostic] {msg}")


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------
# Step 1: Stops
# --------------------------------------------------------------------------

def build_stops(stops_df):
    """Stops layer -> stops.txt. `stops_df` must already have stop_lat/stop_lon
    columns populated from the point geometry (done by the download step)."""
    df = pd.DataFrame()
    df["stop_id"] = stops_df["GStopID"].astype(str)
    df["stop_name"] = ""  # source has no name field - filled with a placeholder in fix.py
    df["stop_lat"] = stops_df["stop_lat"]
    df["stop_lon"] = stops_df["stop_lon"]
    df["location_type"] = stops_df.get("GStopType")
    df["parent_station"] = stops_df.get("GStopParen")
    df["wheelchair_boarding"] = stops_df.get("GWheelchairBoarding")
    # ParentID's alias says "Platform_code" but the field name suggests a
    # foreign key; keep it as platform_code but flag for a human to confirm.
    if "ParentID" in stops_df.columns:
        df["platform_code"] = stops_df["ParentID"]
    df["_internal_id"] = stops_df["ID"]  # kept only for the join step below, dropped later
    df["_internal_objectid"] = stops_df.get("OBJECTID")
    return df


def stop_id_lookup(stops_internal_df):
    """Build {Stops.ID -> GStopID} and {Stops.OBJECTID -> GStopID} maps, since
    FromStopID/ToStopID in LineVariantElements reference one of these internal
    keys, not the GTFS-facing GStopID string directly."""
    by_id = dict(zip(stops_internal_df["_internal_id"], stops_internal_df["stop_id"]))
    by_oid = dict(zip(stops_internal_df["_internal_objectid"], stops_internal_df["stop_id"]))
    return by_id, by_oid


def resolve_stop_ref(raw_value, by_id, by_oid):
    if raw_value in by_id:
        return by_id[raw_value]
    if raw_value in by_oid:
        return by_oid[raw_value]
    return None


# --------------------------------------------------------------------------
# Step 2: Routes (Lines)
# --------------------------------------------------------------------------

def build_routes(lines_df):
    df = pd.DataFrame()
    df["route_id"] = lines_df["GRouteID"].astype(str)
    df["agency_id"] = KNOWN_AGENCY["agency_id"]
    df["route_short_name"] = ""  # source has no name field - placeholder added in fix.py
    df["route_long_name"] = ""
    df["route_type"] = lines_df.get("GRouteType")
    df["_internal_id"] = lines_df["ID"]
    return df


# --------------------------------------------------------------------------
# Step 3: Calendar / calendar_dates
# --------------------------------------------------------------------------

def build_calendar(calendars_df):
    if calendars_df is None or calendars_df.empty:
        diag("Calendars table returned 0 rows - calendar.txt will be empty. "
             "Falling back to CalendarExceptions for service_id resolution "
             "(see calendar_id_lookup_with_fallback).")
        return pd.DataFrame(columns=[
            "service_id", "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday", "start_date", "end_date", "_internal_id",
        ])
    df = pd.DataFrame()
    df["service_id"] = calendars_df["GServiceID"].astype(str)
    for gtfs_col, src_col in [
        ("monday", "Monday"), ("tuesday", "Tuesday"), ("wednesday", "Wednesday"),
        ("thursday", "Thursday"), ("friday", "Friday"), ("saturday", "Saturday"),
        ("sunday", "Sunday"),
    ]:
        df[gtfs_col] = calendars_df.get(src_col)
    df["start_date"] = calendars_df.get("StartDate")
    df["end_date"] = calendars_df.get("EndDate")
    df["_internal_id"] = calendars_df["ID"]
    return df


def build_calendar_dates(exceptions_df, calendar_df=None):
    """
    Build GTFS calendar_dates.txt from Metro Cali's CalendarExceptions
    and add Colombian national public holidays.

    Metro Cali's CalendarExceptions remain authoritative for exceptions
    explicitly published by Metro Cali.

    Colombian national holidays are added so that the Sunday/holiday
    service pattern can operate on holidays that fall on weekdays.

    For a holiday:
      - services that normally operate that weekday are removed
      - a Sunday/holiday service is added

    The function does NOT create a new service_id. It looks for an
    existing calendar service whose Sunday flag is enabled. This avoids
    assuming that Metro Cali necessarily names that service "DOM_FEST".

    If no Sunday service exists, Colombian holiday exceptions are not
    invented. A diagnostic is emitted instead.
    """

    # Start with the exceptions explicitly supplied by Metro Cali.
    if exceptions_df is None or exceptions_df.empty:
        rows = []
    else:
        rows = []

        for _, row in exceptions_df.iterrows():
            rows.append(
                {
                    "service_id": str(row["GServiceID"]),
                    "date": row.get("ExceptionDate"),
                    "exception_type": row.get("GExceptionType"),
                }
            )

    if calendar_df is None or calendar_df.empty:
        return (
            pd.DataFrame(
                rows,
                columns=["service_id", "date", "exception_type"],
            )
            if rows
            else None
        )

    required_calendar_columns = {
        "service_id",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "start_date",
        "end_date",
    }

    missing = required_calendar_columns - set(calendar_df.columns)

    if missing:
        diag(
            "WARNING: Cannot add Colombian holidays because calendar.txt "
            f"is missing columns: {sorted(missing)}"
        )
        return (
            pd.DataFrame(
                rows,
                columns=["service_id", "date", "exception_type"],
            )
            if rows
            else None
        )

    # Find services that already operate on Sunday.
    sunday_services = calendar_df[
        calendar_df["sunday"].fillna(0).astype(str).isin(["1", "1.0", "True"])
    ]

    if sunday_services.empty:
        diag(
            "WARNING: No Sunday service found in calendar.txt; "
            "Colombian national holiday exceptions were not added."
        )
        return (
            pd.DataFrame(
                rows,
                columns=["service_id", "date", "exception_type"],
            )
            if rows
            else None
        )

    # Normally there should be one Sunday/holiday service. If there are
    # several, choose the first one and report the ambiguity rather than
    # inventing a new service_id.
    if len(sunday_services) > 1:
        diag(
            "WARNING: Multiple Sunday services found in calendar.txt: "
            f"{sunday_services['service_id'].astype(str).tolist()}. "
            f"Using {str(sunday_services.iloc[0]['service_id'])!r} "
            "as the holiday service."
        )

    holiday_service_id = str(sunday_services.iloc[0]["service_id"])

    # Determine the years covered by calendar.txt.
    def parse_gtfs_date(value):
        if pd.isna(value):
            return None

        text = str(value).strip()

        if text.endswith(".0"):
            text = text[:-2]

        return pd.to_datetime(text, format="%Y%m%d").date()

    years = set()

    for value in calendar_df["start_date"]:
        parsed = parse_gtfs_date(value)
        if parsed:
            years.add(parsed.year)

    for value in calendar_df["end_date"]:
        parsed = parse_gtfs_date(value)
        if parsed:
            years.add(parsed.year)

    weekday_columns = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]

    for year in sorted(years):
        for holiday_date, holiday_name in colombian_holidays(year).items():

            date_string = holiday_date.strftime("%Y%m%d")

            # Only modify service calendars that actually cover this date.
            active_services = []

            for _, service in calendar_df.iterrows():
                start_date = parse_gtfs_date(service["start_date"])
                end_date = parse_gtfs_date(service["end_date"])

                if start_date is None or end_date is None:
                    continue

                if not (start_date <= holiday_date <= end_date):
                    continue

                weekday_column = weekday_columns[holiday_date.weekday()]
                value = service[weekday_column]

                if str(value).strip() in {"1", "1.0", "True"}:
                    active_services.append(str(service["service_id"]))

            # If Sunday/holiday service is already active on this date,
            # nothing needs to be added.
            if holiday_service_id in active_services:
                continue

            # Remove weekday service for the holiday.
            for service_id in active_services:
                rows.append(
                    {
                        "service_id": service_id,
                        "date": date_string,
                        "exception_type": 2,
                    }
                )

            # Add Sunday/holiday service.
            rows.append(
                {
                    "service_id": holiday_service_id,
                    "date": date_string,
                    "exception_type": 1,
                }
            )

            diag(
                f"Added Colombian holiday: {date_string} "
                f"({holiday_name}) -> {holiday_service_id}"
            )

    df = pd.DataFrame(
        rows,
        columns=["service_id", "date", "exception_type"],
    )

    if df.empty:
        return None

    # Normalize dates so duplicate detection works even when the source
    # contains datetime objects rather than YYYYMMDD strings.
    def normalize_date(value):
        if pd.isna(value):
            return value

        if hasattr(value, "strftime"):
            return value.strftime("%Y%m%d")

        text = str(value).strip()

        if text.endswith(".0"):
            text = text[:-2]

        # Already YYYYMMDD.
        if len(text) == 8 and text.isdigit():
            return text

        parsed = pd.to_datetime(value, errors="coerce")

        if pd.notna(parsed):
            return parsed.strftime("%Y%m%d")

        return text

    df["date"] = df["date"].map(normalize_date)
    df["service_id"] = df["service_id"].astype(str)

    # GTFS requires at most one exception for a given service_id/date.
    # Preserve the first occurrence, which means Metro Cali's explicit
    # CalendarExceptions remain authoritative if they overlap a generated
    # Colombian holiday exception.
    df = df.drop_duplicates(
        subset=["service_id", "date"],
        keep="first",
    )

    return df.sort_values(
        ["date", "service_id"]
    ).reset_index(drop=True)

def calendar_id_lookup(calendar_internal_df):
    """{Calendars.ID -> GServiceID}"""
    return dict(zip(calendar_internal_df["_internal_id"], calendar_internal_df["service_id"]))


def calendar_id_lookup_with_fallback(calendar_internal_df, calendar_exceptions_df, runs_df):
    """Primary source: Calendars.ID -> GServiceID. If Calendars is empty (or
    doesn't cover every CalendarID that Runs actually reference), fall back
    to CalendarExceptions, which carries both CalendarID and GServiceID
    directly and doesn't require the Calendars table at all. Logs how much
    of Runs' CalendarID space each source actually covers, since a partial
    fallback can still leave trips without a resolvable service_id."""
    primary = calendar_id_lookup(calendar_internal_df)

    fallback = {}
    if calendar_exceptions_df is not None and not calendar_exceptions_df.empty \
            and "CalendarID" in calendar_exceptions_df.columns:
        fallback = dict(zip(
            calendar_exceptions_df["CalendarID"].dropna(),
            calendar_exceptions_df["GServiceID"].astype(str),
        ))

    merged = {**fallback, **primary}  # primary wins on overlap (it's the authoritative source)

    if runs_df is not None and "CalendarID" in runs_df.columns:
        needed = set(runs_df["CalendarID"].dropna())
        covered = needed & set(merged.keys())
        diag(f"service_id coverage: {len(covered)}/{len(needed)} distinct "
             f"CalendarID value(s) referenced by Runs are resolvable "
             f"(Calendars covers {len(needed & set(primary.keys()))}, "
             f"CalendarExceptions fallback adds {len(needed & set(fallback.keys()) - set(primary.keys()))})")
        if len(covered) < 0.9 * len(needed) and len(needed) > 0:
            diag(f"WARNING: {len(needed) - len(covered)} CalendarID value(s) "
                 f"used by Runs have NO service_id anywhere (not in Calendars, "
                 f"not in CalendarExceptions). Trips using them will be "
                 f"dropped. This likely means the live 'Calendars' table "
                 f"query needs manual investigation - see the [diagnostic] "
                 f"lines above about server record counts.")

    return merged


# --------------------------------------------------------------------------
# Step 4: pattern chain (LineVariants + LineVariantElements) -> shapes + stop
#          sequence per pattern
# --------------------------------------------------------------------------

def build_line_variant_lookup(line_variants_df, lines_internal_id_to_route_id):
    """{LineVariants.ID -> {route_id, direction_id, shape_id}}"""
    out = {}
    for _, row in line_variants_df.iterrows():
        route_id = lines_internal_id_to_route_id.get(row["LineID"])
        out[row["ID"]] = {
            "route_id": route_id,
            "direction_id": row.get("GDirectionID"),
            "shape_id": row.get("GShapeID"),
        }
    return out


def build_stop_sequence_per_pattern(line_variant_elements_df, by_id, by_oid):
    """For each LineVarID, return an ordered list of resolved GTFS stop_ids,
    built by walking the FromStopID -> ToStopID chain in SqIdx order.
    Also returns, per pattern, the ordered raw geometry paths for shapes.txt."""
    sequences = {}   # LineVarID -> [stop_id, stop_id, ...]  (N stops from N-1 segments)
    geometries = {}  # LineVarID -> [(lat, lon), ...] concatenated path points, in order
    unresolved = 0

    for line_var_id, group in line_variant_elements_df.groupby("LineVarID"):
        group = group.sort_values("SqIdx")
        stops = []
        pts = []
        for i, (_, seg) in enumerate(group.iterrows()):
            from_sid = resolve_stop_ref(seg["FromStopID"], by_id, by_oid)
            to_sid = resolve_stop_ref(seg["ToStopID"], by_id, by_oid)
            if from_sid is None or to_sid is None:
                unresolved += 1
            if i == 0:
                stops.append(from_sid)
            stops.append(to_sid)
            seg_pts = seg.get("_geom_path") or []
            if i == 0:
                pts.extend(seg_pts)
            else:
                pts.extend(seg_pts[1:])  # avoid duplicating the shared vertex
        sequences[line_var_id] = stops
        geometries[line_var_id] = pts

    if unresolved:
        diag(f"WARNING: {unresolved} FromStopID/ToStopID values in "
             f"LineVariantElements did not resolve to a known Stops.ID/OBJECTID")
    return sequences, geometries


def build_shapes(line_variant_lookup, geometries):
    """One shapes.txt row set per distinct GShapeID (first LineVariant
    encountered for that shape_id wins; logs if multiple patterns share a
    shape_id with materially different geometry)."""
    rows = []
    seen_shape_ids = {}
    for lv_id, meta in line_variant_lookup.items():
        shape_id = meta.get("shape_id")
        if not shape_id:
            continue
        pts = geometries.get(lv_id, [])
        if not pts:
            continue
        if shape_id in seen_shape_ids:
            continue  # already built from another LineVariant sharing this shape_id
        seen_shape_ids[shape_id] = lv_id
        cum = 0.0
        prev = None
        for seq, (lat, lon) in enumerate(pts, start=1):
            if prev is not None:
                cum += haversine_m(prev[0], prev[1], lat, lon)
            rows.append({
                "shape_id": shape_id, "shape_pt_lat": lat, "shape_pt_lon": lon,
                "shape_pt_sequence": seq, "shape_dist_traveled": round(cum, 1),
            })
            prev = (lat, lon)
    return pd.DataFrame(rows) if rows else None


# --------------------------------------------------------------------------
# Step 5: detect the two ambiguous things before trusting stop_times
# --------------------------------------------------------------------------

def detect_schedule_alignment(schedule_elements_df, schedules_df, stop_sequences):
    """For each Schedule, compare len(ScheduleElements rows) against
    len(stop sequence) for its LineVariant. If it matches len(stops), the
    ScheduleElements are one-per-stop ("per_stop"). If it matches
    len(stops)-1, they're one-per-segment ("per_segment"). Majority vote
    across all schedules decides; ties/ambiguity are reported loudly."""
    if SCHEDULE_ALIGNMENT_OVERRIDE:
        diag(f"Using SCHEDULE_ALIGNMENT_OVERRIDE = '{SCHEDULE_ALIGNMENT_OVERRIDE}'")
        return SCHEDULE_ALIGNMENT_OVERRIDE

    schedule_to_linevar = dict(zip(schedules_df["ID"], schedules_df["LineVarID"]))
    se_counts = schedule_elements_df.groupby("ScheduleID").size()

    votes = Counter()
    examples = {"per_stop": None, "per_segment": None, "unknown": None}
    for schedule_id, n_elements in se_counts.items():
        line_var_id = schedule_to_linevar.get(schedule_id)
        n_stops = len(stop_sequences.get(line_var_id, []))
        if n_stops == 0:
            continue
        if n_elements == n_stops:
            votes["per_stop"] += 1
            examples["per_stop"] = examples["per_stop"] or (schedule_id, n_elements, n_stops)
        elif n_elements == n_stops - 1:
            votes["per_segment"] += 1
            examples["per_segment"] = examples["per_segment"] or (schedule_id, n_elements, n_stops)
        else:
            votes["unknown"] += 1
            examples["unknown"] = examples["unknown"] or (schedule_id, n_elements, n_stops)

    diag(f"schedule-alignment vote tally: {dict(votes)}")
    for kind, ex in examples.items():
        if ex:
            diag(f"  example ({kind}): ScheduleID={ex[0]}, "
                 f"ScheduleElements rows={ex[1]}, stops in pattern={ex[2]}")

    if not votes:
        raise RuntimeError("Could not determine schedule alignment - no comparable "
                            "Schedule/LineVariant pairs found. Set "
                            "SCHEDULE_ALIGNMENT_OVERRIDE in gtfs_common.py manually.")

    winner, winner_votes = votes.most_common(1)[0]
    total = sum(votes.values())
    if winner_votes < 0.6 * total:
        raise RuntimeError(
            f"Schedule alignment is ambiguous ({dict(votes)}). Inspect the "
            f"printed examples above and set SCHEDULE_ALIGNMENT_OVERRIDE in "
            f"gtfs_common.py to 'per_stop' or 'per_segment' manually."
        )
    diag(f"Detected schedule alignment: '{winner}' ({winner_votes}/{total} patterns agree)")
    return winner


def detect_time_unit(runs_df, schedule_elements_df, schedules_df):
    """Sample StartRun + max(Arrival) per run to guess whether offsets are
    in seconds, minutes, or decimal hours. Assumes service days are <= ~30h
    (to allow for modest post-midnight trips)."""
    if TIME_UNIT_OVERRIDE:
        diag(f"Using TIME_UNIT_OVERRIDE = '{TIME_UNIT_OVERRIDE}'")
        return TIME_UNIT_OVERRIDE

    max_arrival_by_schedule = schedule_elements_df.groupby("ScheduleID")["Arrival"].max()
    sample = runs_df.head(2000).copy()
    sample["max_arrival"] = sample["ScheduleID"].map(max_arrival_by_schedule)
    sample = sample.dropna(subset=["max_arrival"])
    sample["raw_end"] = sample["StartRun"].astype(float) + sample["max_arrival"].astype(float)

    if sample.empty:
        raise RuntimeError("Could not sample any Run to detect the time unit. "
                            "Set TIME_UNIT_OVERRIDE in gtfs_common.py manually.")

    p95 = sample["raw_end"].quantile(0.95)
    diag(f"time-unit detection: 95th percentile of (StartRun + max Arrival) = {p95:.2f}")

    if p95 <= 30:
        unit = "hours"
    elif p95 <= 1800:
        unit = "minutes"
    elif p95 <= 130000:
        unit = "seconds"
    else:
        raise RuntimeError(
            f"Detected end-of-day value ({p95:.1f}) doesn't fit hours, minutes, "
            f"or seconds. Inspect raw StartRun/Arrival/Departure values directly "
            f"and set TIME_UNIT_OVERRIDE in gtfs_common.py manually."
        )
    diag(f"Detected time unit: '{unit}'")
    return unit


def to_seconds(value, unit):
    value = float(value)
    return value * {"hours": 3600, "minutes": 60, "seconds": 1}[unit]


def seconds_to_gtfs_time(total_seconds):
    total_seconds = int(round(total_seconds))
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# --------------------------------------------------------------------------
# Step 6: trips.txt + stop_times.txt
# --------------------------------------------------------------------------

def build_trips_and_stop_times(
    runs_df, schedules_df, schedule_elements_df, line_variant_lookup,
    stop_sequences, calendar_id_to_service_id, time_unit, alignment,
):
    schedule_to_linevar = dict(zip(schedules_df["ID"], schedules_df["LineVarID"]))
    se_by_schedule = {sid: g.sort_values("SqIdx") for sid, g in schedule_elements_df.groupby("ScheduleID")}

    trip_rows = []
    stop_time_rows = []
    skipped_no_pattern = 0
    skipped_no_elements = 0
    skipped_no_service = 0

    for _, run in runs_df.iterrows():
        line_var_id = schedule_to_linevar.get(run["ScheduleID"])
        meta = line_variant_lookup.get(line_var_id)
        stops = stop_sequences.get(line_var_id)
        elements = se_by_schedule.get(run["ScheduleID"])
        service_id = calendar_id_to_service_id.get(run.get("CalendarID"))

        if meta is None or not stops:
            skipped_no_pattern += 1
            continue
        if elements is None or elements.empty:
            skipped_no_elements += 1
            continue
        if not service_id:
            skipped_no_service += 1
            continue

        trip_id = str(run["GTripID"])
        start_run = to_seconds(run["StartRun"], time_unit)

        times = []  # (stop_id, arrival_sec, departure_sec)
        elements = elements.reset_index(drop=True)

        if alignment == "per_stop":
            n = min(len(stops), len(elements))
            for i in range(n):
                stop_id = stops[i]
                arr = start_run + to_seconds(elements.loc[i, "Arrival"], time_unit)
                dep = start_run + to_seconds(elements.loc[i, "Departure"], time_unit)
                times.append((stop_id, arr, dep))
        else:  # per_segment: element i covers travel FROM stop i TO stop i+1
            n = min(len(stops) - 1, len(elements))
            # stop 0: departure = element[0].Departure, arrival = same (origin)
            dep0 = start_run + to_seconds(elements.loc[0, "Departure"], time_unit)
            times.append((stops[0], dep0, dep0))
            for i in range(n):
                arr = start_run + to_seconds(elements.loc[i, "Arrival"], time_unit)
                dep = arr if i == n - 1 else start_run + to_seconds(elements.loc[i + 1, "Departure"], time_unit)
                times.append((stops[i + 1], arr, dep))

        if len(times) < 2:
            continue

        trip_rows.append({
            "route_id": meta["route_id"],
            "service_id": service_id,
            "trip_id": trip_id,
            "direction_id": meta.get("direction_id"),
            "shape_id": meta.get("shape_id"),
            "wheelchair_accessible": run.get("GWheelchairAccessible"),
            "bikes_allowed": run.get("GBikesAllowed"),
        })
        for seq, (stop_id, arr_s, dep_s) in enumerate(times, start=1):
            stop_time_rows.append({
                "trip_id": trip_id,
                "arrival_time": seconds_to_gtfs_time(arr_s),
                "departure_time": seconds_to_gtfs_time(dep_s),
                "stop_id": stop_id,
                "stop_sequence": seq,
            })

    if skipped_no_pattern or skipped_no_elements or skipped_no_service:
        diag(f"Runs skipped - no pattern/shape: {skipped_no_pattern}, "
             f"no ScheduleElements: {skipped_no_elements}, "
             f"no matching service_id: {skipped_no_service}")

    trips_df = pd.DataFrame(trip_rows)
    stop_times_df = pd.DataFrame(stop_time_rows)
    return trips_df, stop_times_df
