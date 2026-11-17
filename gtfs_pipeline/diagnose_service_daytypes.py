"""
Diagnose which day type (HABIL / SABADO / DOM_FEST) each real service_id is.
============================================================================
The source feed has 4 internal service_ids (8099041, 8099042, 8099043, 8099148)
and no calendar. The published Metro Cali route poster (data/route_schedule_pdf.csv,
PDF dated Jan 2026) gives, per route, the operating window for L-V, Sabado and
Domingo/Festivo. This script compares, per service_id, the first/last departure
of every route with those windows and proposes a service_id -> day-type mapping.

Reference windows come from schedule_reference.py (live listadoRutas.php, falling back to
a dated snapshot and the poster).

Run AFTER enrich_routes.py (routes.txt must carry the letter codes, e.g. A01A,
in route_short_name) and BEFORE fix.py:

    python diagnose_service_daytypes.py [raw_dir] [pdf_csv]

Outputs (build/report/):
    service_id_daytype_diagnostic.csv   score per (service_id, day type)
    service_id_mapping_proposed.json    {"8099041": "HABIL", ...} + confidence
It is a DIAGNOSTIC: it never changes the feed. Review the proposal, then copy
the mapping into validity_override.SERVICE_DAYTYPE.
"""
import json
import os
import re
import sys

import numpy as np
import pandas as pd

import schedule_reference as SR
from gtfs_common import RAW_DIR, REPORT_DIR
from schedule_reference import parse_window

DAYTYPES = ["HABIL", "SABADO", "DOM_FEST"]
PDF_COLS = {"HABIL": "habil", "SABADO": "sabado", "DOM_FEST": "dom_fest"}
DEFAULT_PDF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "route_schedule_pdf.csv")
TOLERANCE_MIN = 180      # deviation (first+last) at which fit drops to 0
MIN_TRIPS_PER_ROUTE = 3  # ignore routes with too few trips in a service_id
AMBIGUOUS_MARGIN = 0.05


def hhmm_to_min(s):
    h, m = s.split(":")[:2]
    return int(h) * 60 + int(m)


def load_pdf_table(path):
    """Kept for compatibility: the poster-only table."""
    return SR.table_from_pdf_csv(path)


def pdf_windows_for(code, table):
    """Exact code, else union of all PDF codes starting with it (A01 -> A01A+A01B)."""
    code = code.strip().upper()
    if code in table:
        return table[code]
    cands = [v for k, v in table.items() if k.startswith(code)]
    if not cands:
        return None
    out = {}
    for dt in DAYTYPES:
        ws = [c[dt] for c in cands if c[dt]]
        out[dt] = (min(w[0] for w in ws), max(w[1] for w in ws)) if ws else None
    return out


def trip_spans(stop_times):
    """first and last departure (minutes) per trip."""
    st = stop_times.copy()
    st["m"] = st["departure_time"].map(lambda s: hhmm_to_min(s) if isinstance(s, str) and ":" in s else np.nan)
    g = st.dropna(subset=["m"]).groupby("trip_id")["m"].agg(["min", "max"])
    return g.rename(columns={"min": "first", "max": "last"})


def route_fit(first, last, window):
    if window is None:
        return 0.0                       # PDF says the route does not run that day
    dev = abs(first - window[0]) + abs(last - window[1])
    return max(0.0, 1.0 - dev / TOLERANCE_MIN)


def score_service_ids(trips, spans, routes, pdf_table):
    """Returns (detail_df, scores_df, unmatched_codes)."""
    t = trips[["trip_id", "route_id", "service_id"]].merge(spans, left_on="trip_id", right_index=True)
    t = t.merge(routes[["route_id", "route_short_name"]], on="route_id", how="left")
    t["code"] = t["route_short_name"].fillna("").astype(str).str.strip().str.upper()
    rows, unmatched = [], set()
    for (sid, code), g in t.groupby(["service_id", "code"]):
        if not code or len(g) < MIN_TRIPS_PER_ROUTE:
            continue
        wins = pdf_windows_for(code, pdf_table)
        if wins is None:
            unmatched.add(code)
            continue
        first, last = g["first"].min(), g["last"].max()
        row = {"service_id": sid, "route": code, "trips": len(g),
               "first": f"{int(first)//60:02d}:{int(first)%60:02d}",
               "last": f"{int(last)//60:02d}:{int(last)%60:02d}"}
        for dt in DAYTYPES:
            row[dt] = round(route_fit(first, last, wins[dt]), 3)
        rows.append(row)
    detail = pd.DataFrame(rows)
    if detail.empty:
        return detail, pd.DataFrame(), unmatched
    scores = detail.groupby("service_id")[DAYTYPES].mean()
    scores["routes"] = detail.groupby("service_id").size()
    scores["trips"] = detail.groupby("service_id")["trips"].sum()
    return detail, scores, unmatched


def propose_mapping(scores):
    """Best day type per service_id with a confidence label."""
    out = {}
    for sid, r in scores.iterrows():
        ranked = sorted(DAYTYPES, key=lambda d: -r[d])
        best, second = ranked[0], ranked[1]
        margin = float(r[best] - r[second])
        out[str(sid)] = {
            "daytype": best, "score": round(float(r[best]), 3), "margin": round(margin, 3),
            "runner_up": second,
            "confidence": "ambiguous" if margin < AMBIGUOUS_MARGIN else ("high" if margin > 0.15 else "medium"),
            "trips": int(r["trips"]), "routes": int(r["routes"]),
        }
    # Tie-break for HABIL vs SABADO: a weekday service has more trips than a Saturday one.
    amb = [s for s, v in out.items() if v["confidence"] == "ambiguous"
           and {v["daytype"], v["runner_up"]} == {"HABIL", "SABADO"}]
    if len(amb) >= 2:
        by_trips = sorted(amb, key=lambda s: -out[s]["trips"])
        out[by_trips[0]].update(daytype="HABIL", confidence="tie-break by trip count (heuristic)")
        for s in by_trips[1:]:
            out[s].update(daytype="SABADO", confidence="tie-break by trip count (heuristic)")
    return out


def main(raw_dir=RAW_DIR, pdf_csv=DEFAULT_PDF):
    for f in ("trips.txt", "stop_times.txt", "routes.txt"):
        if not os.path.exists(os.path.join(raw_dir, f)):
            sys.exit(f"ERROR: {raw_dir}/{f} not found. Run download.py (and enrich_routes.py) first.")
    routes = pd.read_csv(os.path.join(raw_dir, "routes.txt"), dtype=str)
    if "route_short_name" not in routes.columns or routes["route_short_name"].fillna("").eq("").all():
        sys.exit("ERROR: routes.txt has no route_short_name. Run enrich_routes.py first "
                 "(it maps internal route_ids to letter codes like A01A).")
    trips = pd.read_csv(os.path.join(raw_dir, "trips.txt"), dtype=str)
    st = pd.read_csv(os.path.join(raw_dir, "stop_times.txt"), dtype=str,
                     usecols=["trip_id", "departure_time"])
    pdf_table, sources = SR.load_reference(pdf_csv=pdf_csv)
    from collections import Counter
    print(f"Schedule reference: {len(pdf_table)} routes, sources: {dict(Counter(sources.values()))} "
          f"(live/snapshot = Metro Cali listadoRutas.php, pdf = poster of Jan 2026 for the rest)")

    detail, scores, unmatched = score_service_ids(trips, trip_spans(st), routes, pdf_table)
    if detail.empty:
        sys.exit("ERROR: no (service_id, route) pair could be matched to the PDF table. "
                 "Check route_short_name values in routes.txt.")
    if unmatched:
        print(f"  routes not found in PDF ({len(unmatched)}): {sorted(unmatched)[:20]}")

    os.makedirs(REPORT_DIR, exist_ok=True)
    detail.to_csv(os.path.join(REPORT_DIR, "service_id_daytype_diagnostic.csv"), index=False)
    mapping = propose_mapping(scores)
    with open(os.path.join(REPORT_DIR, "service_id_mapping_proposed.json"), "w") as f:
        json.dump(mapping, f, indent=2)

    print("\n=== Mean fit per service_id (1.0 = matches the reference window perfectly) ===")
    print(scores.round(3).to_string())
    print("\n=== Proposed mapping ===")
    for sid, v in mapping.items():
        print(f"  {sid} -> {v['daytype']:<8} score={v['score']} margin={v['margin']} "
              f"({v['confidence']}; runner-up {v['runner_up']}; {v['trips']} trips on {v['routes']} routes)")
    print("\nDistinguishing routes (Saturday differs from weekday in the reference), per service_id:")
    for code in ("A11", "A11B", "E37", "E51", "A14A", "A14B"):
        d = detail[detail["route"].str.startswith(code)]
        for _, r in d.iterrows():
            print(f"  {r['service_id']} {r['route']:<5} {r['first']}-{r['last']} ({r['trips']} trips) "
                  f"H={r['HABIL']} S={r['SABADO']} D={r['DOM_FEST']}")
    print("\nCompare with validity_override.SERVICE_DAYTYPE and update it if the evidence changes.")
    return mapping


if __name__ == "__main__":
    main(*sys.argv[1:3])
