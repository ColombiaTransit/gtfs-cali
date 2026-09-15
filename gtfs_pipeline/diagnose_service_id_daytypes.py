"""
diagnose_service_id_daytypes.py
================================
A second, independent source of evidence for what Metro Cali's real
service_id values (e.g. "8099041") actually mean, complementing
diagnose_service_ids.py's exception-date evidence.

The separate "rutas" dataset publishes, per route, the operating-hour
WINDOW for each day-type (HABIL=weekday, SABADO=Saturday, DOM_FEST=Sunday
+festivos) via its HABIL/SABADO/DOM_FEST columns. Idea: for every
(route_id, service_id) combination actually used in trips.txt, compute
that group's real trip-time SPAN (earliest to latest departure across all
its trips, from stop_times.txt) and compare it against that route's three
published day-type windows - reusing the route_id -> real letter-code
mapping routes_enrich.py already wrote into routes.txt's route_short_name.

Whichever window a (route, service_id) group's span fits best is evidence
- not proof - for that service_id's day-type, for that one route.
Aggregating the vote across many different routes per service_id turns
weak, single-route evidence into a much stronger signal if the pattern
holds consistently; a service_id whose best-fit flips between HABIL and
DOM_FEST route to route is telling you the opposite - don't trust it.

IMPORTANT: the exact text format of the HABIL/SABADO/DOM_FEST columns
hasn't been observed on live data yet. parse_time_window() is
deliberately permissive (handles "05:00 - 22:00", "5:00 AM - 10:00 PM",
"05:00-22:00", etc. via a generic time-token extractor) and returns None
- logged, not silently dropped - for anything it can't parse, rather than
guessing.

No network calls in here - see run_diagnose_service_id_daytypes.py for
the CLI wrapper that fetches rutas and reads the local GTFS files.
"""
import re

import pandas as pd

import routes_enrich as RE

DAY_TYPES = ["HABIL", "SABADO", "DOM_FEST"]

# Real trip departures commonly run a bit earlier/later than a published
# window (schedule padding, last-bus-of-the-day rounding) - this much
# slack is allowed before treating a span as "not contained" by a window.
CONTAINMENT_TOLERANCE_SEC = 30 * 60

DIAGNOSTICS = []


def diag(msg):
    DIAGNOSTICS.append(msg)
    print(f"  [diagnostic] {msg}")


_TIME_TOKEN_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AaPp]\.?\s?[Mm]\.?)?")


def _token_to_seconds(hh, mm, ampm):
    h, m = int(hh), int(mm)
    if ampm:
        ampm_l = ampm.lower().replace(".", "").replace(" ", "")
        if ampm_l.startswith("p") and h != 12:
            h += 12
        if ampm_l.startswith("a") and h == 12:
            h = 0
    return h * 3600 + m * 60


def parse_time_window(text):
    """'05:00 - 22:00' / '5:00 AM - 10:00 PM' / '05:00-22:00' -> (start_sec,
    end_sec). Returns None for blank/unparseable text (logs which, if a
    non-blank value couldn't be parsed, since that likely means the real
    format differs from what's handled here and needs a look)."""
    if pd.isna(text):
        return None
    s = str(text).strip()
    if not s or s.lower() in ("nan", "none", "-", "n/a"):
        return None
    tokens = _TIME_TOKEN_RE.findall(s)
    if len(tokens) < 2:
        diag(f"could not parse a time window out of '{s}' (found {len(tokens)} "
             f"time-like token(s), need 2) - update parse_time_window if this "
             f"is actually a valid window in a format not yet handled")
        return None
    return (_token_to_seconds(*tokens[0]), _token_to_seconds(*tokens[1]))


def extract_route_daytype_windows(ext_df):
    """rutas rows (already fetched, ideally already filtered to VARIANTE ==
    NORMAL - see routes_enrich.filter_to_normal_variant) -> {base_route_code:
    {"HABIL": (start,end) or None, "SABADO": ..., "DOM_FEST": ...}}."""
    normal_df = RE.filter_to_normal_variant(ext_df)
    windows = {}
    for idx, row in normal_df.iterrows():
        base = RE.base_route_code(row.get("RUTA"))
        if not base:
            continue
        entry = windows.setdefault(base, {dt: None for dt in DAY_TYPES})
        for dt in DAY_TYPES:
            if entry[dt] is None and dt in normal_df.columns:
                parsed = parse_time_window(row.get(dt))
                if parsed:
                    entry[dt] = parsed
    n_with_any = sum(1 for w in windows.values() if any(w.values()))
    diag(f"extracted day-type windows for {n_with_any}/{len(windows)} base "
         f"route code(s) with at least one parseable window")
    return windows


def gtfs_time_to_seconds(t):
    """'06:15:00' -> 22500. Tolerates times past 24:00:00 (post-midnight
    trips) since that's valid GTFS."""
    h, m, s = str(t).strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def compute_trip_spans(trips_df, stop_times_df):
    """Returns a DataFrame, one row per (route_id, service_id) actually
    used together in trips.txt: the earliest and latest departure time
    across ALL trips in that group (the group's real service span), and
    how many trips it's based on."""
    st = stop_times_df.copy()
    st["dep_sec"] = st["departure_time"].map(gtfs_time_to_seconds)
    per_trip = st.groupby("trip_id")["dep_sec"].agg(["min", "max"]).reset_index()
    per_trip.columns = ["trip_id", "trip_start_sec", "trip_end_sec"]

    merged = trips_df[["trip_id", "route_id", "service_id"]].merge(per_trip, on="trip_id", how="inner")
    spans = merged.groupby(["route_id", "service_id"]).agg(
        span_start_sec=("trip_start_sec", "min"),
        span_end_sec=("trip_end_sec", "max"),
        n_trips=("trip_id", "nunique"),
    ).reset_index()
    return spans


def score_window_fit(span_start, span_end, window):
    """Lower diff_sec = better fit. contained=True means the span sits
    inside the window (within CONTAINMENT_TOLERANCE_SEC slack)."""
    if window is None:
        return None
    w_start, w_end = window
    diff = abs(span_start - w_start) + abs(span_end - w_end)
    contained = (span_start >= w_start - CONTAINMENT_TOLERANCE_SEC) and \
                (span_end <= w_end + CONTAINMENT_TOLERANCE_SEC)
    return {"diff_sec": diff, "contained": contained}


def analyze(route_daytype_windows, trip_spans_df, route_id_to_base_code):
    """Per (route_id, service_id) group, picks the best-fitting day-type
    window (if the route has any published windows at all). Returns a
    DataFrame with one row per group - the raw, per-route evidence, before
    aggregation."""
    rows = []
    for _, r in trip_spans_df.iterrows():
        base = route_id_to_base_code.get(r["route_id"])
        windows = route_daytype_windows.get(base) if base else None
        row = r.to_dict()
        row["base_code"] = base

        scores = {}
        if windows:
            for dt in DAY_TYPES:
                fit = score_window_fit(r["span_start_sec"], r["span_end_sec"], windows.get(dt))
                if fit:
                    scores[dt] = fit

        if scores:
            best_dt = min(scores, key=lambda k: scores[k]["diff_sec"])
            row["best_daytype"] = best_dt
            row["best_score_sec"] = scores[best_dt]["diff_sec"]
            row["contained"] = scores[best_dt]["contained"]
        else:
            row["best_daytype"] = None
            row["best_score_sec"] = None
            row["contained"] = None
        rows.append(row)
    return pd.DataFrame(rows)


def aggregate_by_service_id(analysis_df):
    """Rolls the per-route evidence up to one row per service_id: a vote
    tally across all routes that had evidence, the top day-type, and a
    confidence = (votes for top) / (routes with any evidence). A
    service_id with high confidence across many routes is trustworthy
    evidence; one with a near-even split across day-types, or with
    evidence from only one route, is not."""
    if analysis_df.empty:
        return pd.DataFrame()
    results = []
    for sid, group in analysis_df.groupby("service_id"):
        valid = group.dropna(subset=["best_daytype"])
        votes = valid["best_daytype"].value_counts().to_dict()
        n_with_evidence = len(valid)
        top = max(votes, key=votes.get) if votes else None
        confidence = (votes[top] / n_with_evidence) if top and n_with_evidence else 0.0
        results.append({
            "service_id": sid,
            "n_routes_total": len(group),
            "n_routes_with_evidence": n_with_evidence,
            "votes": votes,
            "top_daytype": top,
            "confidence": confidence,
        })
    return pd.DataFrame(results).sort_values(
        ["confidence", "n_routes_with_evidence"], ascending=False
    ).reset_index(drop=True)


def format_report(aggregate_df, analysis_df=None):
    if aggregate_df.empty:
        return "No (route_id, service_id) groups with any day-type window evidence."

    lines = ["=== Day-type window fit diagnostic (rutas HABIL/SABADO/DOM_FEST) ===", ""]
    for _, row in aggregate_df.iterrows():
        lines.append(f"service_id = {row['service_id']}")
        lines.append(f"  routes with published windows: {row['n_routes_with_evidence']}/"
                      f"{row['n_routes_total']}")
        if row["top_daytype"]:
            lines.append(f"  best-fit day-type: {row['top_daytype']} "
                          f"(confidence {row['confidence']:.0%}, votes: {row['votes']})")
        else:
            lines.append("  no day-type evidence available for any route this service_id uses")
        lines.append("")

    lines.append("Read confidence as: fraction of routes-with-evidence that agreed on the "
                  "same day-type. High confidence across many routes = trustworthy; low "
                  "confidence, or evidence from only one or two routes, is weak and shouldn't "
                  "be relied on alone. Cross-check against diagnose_service_ids.py's "
                  "exception-date evidence before drawing a conclusion.")
    return "\n".join(lines)
