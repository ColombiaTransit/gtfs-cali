"""
Variant stop-pattern report (informational, never changes the feed)
====================================================================
The source schedule carries Sunday/festivo variants (Ciclovia 07:00-14:00, Normal, Desvio ...) as
different stop lists on trips of the same route. This report shows, per route and direction, the
distinct stop lists of the day type's trips, how many trips use each, when they depart, and which
stops a pattern adds or drops compared with the dominant one.

Output: build/report/variant_stop_patterns.csv
  route_short_name, daytype, direction_id, pattern_no, is_dominant, n_trips, n_stops, first_dep, last_dep,
  added_vs_dominant, removed_vs_dominant, first_stop, last_stop, ciclovia_window, in_window_pct

in_window_pct = share of the pattern's trips that depart inside the Ciclovia window from listadoRutas.php
(+/- TOLERANCE_MIN, because a trip leaving shortly before 07:00 reaches the detour after 07:00).
A weekday pattern is only reported when it differs from the dominant one as well.
"""
import os
import sys
import warnings

import pandas as pd

import schedule_reference as SR
import validity_override as VO
from gtfs_common import CLEAN_DIR, RAW_DIR, REPORT_DIR
from routes_from_codes import split_variante

TOLERANCE_MIN = 15
DAYTYPES = ("DOM_FEST", "SABADO", "HABIL")
COLUMNS = ["route_short_name", "daytype", "direction_id", "pattern_no", "is_dominant", "n_trips", "n_stops",
           "first_dep", "last_dep", "added_vs_dominant", "removed_vs_dominant", "first_stop", "last_stop",
           "ciclovia_window", "in_window_pct"]


def to_minutes(t):
    try:
        h, m, _ = str(t).split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def hhmm(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def ciclovia_windows(listado_rows):
    """{route_code: (start_min, end_min)} from the Ciclovia variant rows (dom_fest column)."""
    out = {}
    for r in listado_rows or []:
        label, _ = split_variante(r.get("variante"))
        if "ciclov" not in label:
            continue
        w = SR.parse_window(r.get("dom_fest"))
        if w:
            code = str(r.get("route_code", "")).strip().upper()
            lo, hi = out.get(code, w)
            out[code] = (min(lo, w[0]), max(hi, w[1]))
    return out


def build_report(routes, trips, stop_times, stops, service_daytype=None, windows=None):
    service_daytype = VO.SERVICE_DAYTYPE if service_daytype is None else service_daytype
    windows = windows or {}
    names = dict(zip(stops["stop_id"], stops["stop_name"]))
    code = dict(zip(routes["route_id"], routes["route_short_name"]))
    t = trips[["trip_id", "route_id", "service_id", "direction_id"]].copy()
    t["daytype"] = t["service_id"].map(service_daytype)
    t = t[t["daytype"].isin(DAYTYPES)]
    st = stop_times[stop_times["trip_id"].isin(set(t["trip_id"]))].copy()
    st["seq"] = pd.to_numeric(st["stop_sequence"])
    st = st.sort_values(["trip_id", "seq"])
    per_trip = st.groupby("trip_id").agg(stops_=("stop_id", tuple), dep=("departure_time", "first"))
    t = t.merge(per_trip, left_on="trip_id", right_index=True)
    t["dep_min"] = t["dep"].map(to_minutes)
    t["route_short_name"] = t["route_id"].map(code).fillna(t["route_id"])

    rows = []
    for (rc, dt, d), g in t.groupby(["route_short_name", "daytype", "direction_id"], dropna=False):
        pats = g.groupby("stops_")
        ordered = sorted(pats, key=lambda kv: (-len(kv[1]), min(kv[1]["dep_min"].dropna(), default=0)))
        if len(ordered) < 2:
            continue
        dominant = set(ordered[0][0])
        win = windows.get(str(rc).upper())
        for n, (seq, tr) in enumerate(ordered, start=1):
            deps = tr["dep_min"].dropna()
            pct = ""
            if win and len(deps):
                pct = round(100 * ((deps >= win[0] - TOLERANCE_MIN) & (deps <= win[1])).mean())
            rows.append({
                "route_short_name": rc, "daytype": dt, "direction_id": d, "pattern_no": n,
                "is_dominant": n == 1, "n_trips": len(tr), "n_stops": len(seq),
                "first_dep": hhmm(int(deps.min())) if len(deps) else "", "last_dep": hhmm(int(deps.max())) if len(deps) else "",
                "added_vs_dominant": " | ".join(names.get(s, s) for s in seq if s not in dominant),
                "removed_vs_dominant": " | ".join(names.get(s, s) for s in ordered[0][0] if s not in set(seq)),
                "first_stop": names.get(seq[0], seq[0]), "last_stop": names.get(seq[-1], seq[-1]),
                "ciclovia_window": f"{hhmm(win[0])}-{hhmm(win[1])}" if win else "", "in_window_pct": pct})
    return pd.DataFrame(rows, columns=COLUMNS)


def main(feed_dir=None, report_dir=REPORT_DIR):
    feed_dir = feed_dir or (CLEAN_DIR if os.path.exists(os.path.join(CLEAN_DIR, "trips.txt")) else RAW_DIR)
    read = lambda f: pd.read_csv(os.path.join(feed_dir, f), dtype=str, keep_default_na=False)
    routes, trips, stops = read("routes.txt"), read("trips.txt"), read("stops.txt")
    stop_times = pd.read_csv(os.path.join(feed_dir, "stop_times.txt"), dtype=str, keep_default_na=False,
                             usecols=["trip_id", "stop_id", "stop_sequence", "departure_time"])
    rows, label = SR.load_listado_rows()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rep = build_report(routes, trips, stop_times, stops, windows=ciclovia_windows(rows))
    os.makedirs(report_dir, exist_ok=True)
    rep.to_csv(os.path.join(report_dir, "variant_stop_patterns.csv"), index=False)
    multi = rep[rep["daytype"] == "DOM_FEST"]["route_short_name"].nunique()
    print(f"variant report ({feed_dir}, listado: {label or 'none'}): {len(rep)} pattern row(s); "
          f"{multi} route(s) have more than one Sunday/festivo stop list per direction")
    return rep


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
