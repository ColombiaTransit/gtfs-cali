"""
Shapes from the ArcGIS `rutas` layer
====================================
The shapes the source schedule delivers are only the stops joined by straight lines (one point per stop,
about 300 m apart). The `rutas` layer holds the real alignment per route variant (Hábil Normal, Domingo
Normal, Domingo Ciclovía, Desvío ...), one polyline for both directions.

For every distinct stop list in the feed (route x day type x direction) this step
  1. takes the variants of that route that run on that day type (the HABIL / SABADO / DOM_FEST hours of the
     variant are not "NO OPERA"),
  2. lays the stops, in trip order, along each variant's polyline: every stop is matched to a place on the
     line that lies after the previous stop's place (so the line is cut into its two directions at the turning
     point, and a short trip gets just its own stretch),
  3. picks the variant with the fewest stops that do not lie on the line; a tie goes to the variant whose
     operating hours contain the departures of those trips (this is how Ciclovía 07:00-14:00 is told from Normal),
     then to the Normal variant,
  4. writes that stretch of the polyline as the trips' shape.
A stop list that does not fit any variant well keeps its old shape. Nothing changes without the rutas layer.

Report: build/report/shape_assignment.csv
"""
import os
import re
import sys
import zipfile
from collections import defaultdict

import numpy as np
import pandas as pd

import validity_override as VO

COS_LAT = float(np.cos(np.radians(3.45)))
M_LON, M_LAT = 111320.0 * COS_LAT, 110574.0
STOP_RADIUS = 60.0        # a stop can be matched to the line within this distance (m)
ON_LINE = 40.0            # a stop counts as lying on the line within this distance (m)
SKIP_COST = 150.0         # price of leaving a stop unmatched
MAX_SKIP = 2              # consecutive stops in the middle that may stay unmatched
MAX_SKIP_END = 12         # at most this many (and at most half the) stops at each end of the trip may lie beyond the polyline
BACKSTEP = 25.0           # allowed step backwards along the line (m), projection noise
MIN_MATCHED = 0.5         # share of the stops that must be matched to accept a variant
MIN_MATCHED_STOPS = 5     # ... and at least this many
MAX_MEAN_DIST = 40.0      # mean distance (m) of the matched stops to the line
WINDOW_TOL_MIN = 15       # departures this much before a window still belong to it
DAYTYPES = {"HABIL": "HABIL", "SABADO": "SABADO", "DOM_FEST": "DOM_FEST", "FESTIVO": "DOM_FEST"}
COLS = {"HABIL": "HABIL", "SABADO": "SABADO", "DOM_FEST": "DOM_FEST"}
RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")
VARIANT_RANK = {"NORMAL": 0, "LARGA": 1, "CORTA": 2, "DESVIO": 3, "CICLOVIA": 4}


def xy(lon, lat):
    return np.asarray(lon) * M_LON, np.asarray(lat) * M_LAT


def parse_ranges(text):
    """'6:00-7:00 / 14:00-22:00' -> [(360, 420), (840, 1320)]; 'NO OPERA' / empty -> None."""
    if not isinstance(text, str) or "NO OPERA" in text.upper():
        return None
    r = [(int(a) * 60 + int(b), int(c) * 60 + int(d)) for a, b, c, d in RANGE.findall(text)]
    return r or None


def in_ranges(minute, ranges, tol=WINDOW_TOL_MIN):
    return any(lo - tol <= minute <= hi for lo, hi in ranges)


# --------------------------------------------------------------------------------------- rutas
class Line:
    """One polyline part in local metres with cumulative length."""

    def __init__(self, lonlat):
        a = np.asarray(lonlat, dtype=float)
        self.lonlat = [tuple(r) for r in a]
        self.lon, self.lat = a[:, 0], a[:, 1]
        self.x, self.y = xy(self.lon, self.lat)
        seg = np.hypot(np.diff(self.x), np.diff(self.y))
        self.seg = seg
        self.s = np.concatenate([[0.0], np.cumsum(seg)])
        self.length = float(self.s[-1])
        self._cache = {}

    def candidates(self, key, lon, lat):
        """Places along the line within STOP_RADIUS of the stop: [(s, dist, lon, lat)] sorted by s."""
        if key in self._cache:
            return self._cache[key]
        px, py = xy(lon, lat)
        ax, ay = self.x[:-1], self.y[:-1]
        dx, dy = np.diff(self.x), np.diff(self.y)
        l2 = np.where(self.seg == 0, 1.0, self.seg ** 2)
        t = np.clip(((px - ax) * dx + (py - ay) * dy) / l2, 0, 1)
        qx, qy = ax + t * dx, ay + t * dy
        d = np.hypot(px - qx, py - qy)
        n = len(d)
        out = []
        for i in range(n):
            if d[i] > STOP_RADIUS:
                continue
            if (i > 0 and d[i - 1] < d[i]) or (i < n - 1 and d[i + 1] < d[i]):
                continue
            out.append((float(self.s[i] + t[i] * self.seg[i]), float(d[i]),
                        float(self.lon[i] + t[i] * (self.lon[i + 1] - self.lon[i])),
                        float(self.lat[i] + t[i] * (self.lat[i + 1] - self.lat[i]))))
        out.sort()
        merged = []
        for c in out:                                  # one place per 40 m of line
            if merged and c[0] - merged[-1][0] < 40:
                if c[1] < merged[-1][1]:
                    merged[-1] = c
            else:
                merged.append(c)
        self._cache[key] = merged
        return merged

    def slice(self, s0, s1):
        """Points of the line between s0 and s1 (both on the line), as [(lon, lat)]."""
        def at(s):
            i = int(np.clip(np.searchsorted(self.s, s, side="right") - 1, 0, len(self.seg) - 1))
            f = 0.0 if self.seg[i] == 0 else (s - self.s[i]) / self.seg[i]
            return (self.lon[i] + f * (self.lon[i + 1] - self.lon[i]), self.lat[i] + f * (self.lat[i + 1] - self.lat[i]))
        mid = [(self.lon[k], self.lat[k]) for k in range(len(self.s)) if s0 < self.s[k] < s1]
        return [at(s0)] + mid + [at(s1)]


def variants_from_features(features):
    """ArcGIS JSON features of `rutas` (outSR 4326) -> list of variant dicts."""
    out = []
    for f in features:
        a = f.get("attributes", {})
        paths = (f.get("geometry") or {}).get("paths") or []
        parts = [Line(p) for p in paths if len(p) >= 2]
        if len(parts) > 1:      # a polyline cut in pieces: also try the pieces chained in the given order
            parts.append(Line([pt for p in paths if len(p) >= 2 for pt in p]))
        parts += [Line(list(reversed(p.lonlat))) for p in parts]    # a line drawn for one direction also serves the other
        code = str(a.get("RUTA", "")).strip().upper()
        if not code or not parts:
            continue
        out.append({
            "fid": a.get("FID", a.get("OBJECTID")), "code": code,
            "variante": str(a.get("VARIANTE", "")).strip().upper(), "dia_vari": str(a.get("DIA_VARI", "")).strip(),
            "windows": {k: parse_ranges(a.get(c)) for k, c in COLS.items()}, "parts": parts})
    return out


# ------------------------------------------------------------------------------ stop matching
def match_stops(line, stops, key_prefix=""):
    """Lay `stops` [(stop_id, lon, lat)] in order along `line`.
    Returns (matches, mean_dist) with matches = [(k, s, dist, lon, lat)] or None when no consistent placement."""
    n = len(stops)
    end_skip = min(MAX_SKIP_END, n // 2)
    cands = [line.candidates(f"{key_prefix}{sid}", lon, lat) for sid, lon, lat in stops]
    sx = [xy(lon, lat) for _, lon, lat in stops]
    best = {}                                   # (k, j) -> (cost, prev state)
    for k in range(n):
        for j, c in enumerate(cands[k]):
            base = (SKIP_COST * k + c[1], None) if k <= end_skip else (None, None)
            bc, bp = base
            for kp in range(max(0, k - MAX_SKIP - 1), k):
                gap = k - kp - 1
                straight = float(np.hypot(sx[k][0] - sx[kp][0], sx[k][1] - sx[kp][1]))
                for jp, cp in enumerate(cands[kp]):
                    prev = best.get((kp, jp))
                    if prev is None:
                        continue
                    step = c[0] - cp[0]
                    if step < -BACKSTEP or step > 3 * straight + 400:
                        continue
                    cost = prev[0] + SKIP_COST * gap + c[1]
                    if bc is None or cost < bc:
                        bc, bp = cost, (kp, jp)
            if bc is not None:
                best[(k, j)] = (bc, bp)
    end = None
    for (k, j), (cost, _) in best.items():
        if k >= n - 1 - end_skip:
            tot = cost + SKIP_COST * (n - 1 - k)
            if end is None or tot < end[0]:
                end = (tot, (k, j))
    if end is None:
        return None
    chain, state = [], end[1]
    while state is not None:
        chain.append(state)
        state = best[state][1]
    chain.reverse()
    matches = [(k, cands[k][j][0], cands[k][j][1], cands[k][j][2], cands[k][j][3]) for k, j in chain]
    return matches, float(np.mean([m[2] for m in matches]))


def score_variant(variant, stops, minutes, daytype, trips_window_tol=WINDOW_TOL_MIN):
    """Best part of one variant for this stop list, or None."""
    best = None
    for pi, part in enumerate(variant["parts"]):
        r = match_stops(part, stops, key_prefix=f"{variant['fid']}.{pi}.")
        if r is None:
            continue
        matches, mean_d = r
        on = sum(1 for m in matches if m[2] <= ON_LINE)
        misfit = len(stops) - on
        if best is None or (misfit, mean_d) < (best["misfit"], best["mean_d"]):
            best = {"part": part, "matches": matches, "mean_d": mean_d, "misfit": misfit, "matched": len(matches)}
    if best is None:
        return None
    ranges = variant["windows"].get(daytype)
    mins = [m for m in minutes if m is not None]
    best["window_share"] = (sum(1 for m in mins if in_ranges(m, ranges, trips_window_tol)) / len(mins)) if (ranges and mins) else 0.0
    return best


def choose_variant(variants, stops, minutes, daytype):
    """-> (variant, score, runner_up_misfit_delta, reason) or (None, None, None, reason)."""
    scored = []
    for v in variants:
        if v["windows"].get(daytype) is None:
            continue
        s = score_variant(v, stops, minutes, daytype)
        if s is not None:
            scored.append((v, s))
    if not scored:
        runs = [v for v in variants if v["windows"].get(daytype) is not None]
        return None, None, None, ("no variant of this route runs on this day type" if not runs
                                  else "stops cannot be laid along the polyline in trip order")
    scored.sort(key=lambda vs: (vs[1]["misfit"], -round(vs[1]["window_share"], 2),
                                VARIANT_RANK.get(vs[0]["variante"], 9), vs[1]["mean_d"]))
    v, s = scored[0]
    n = len(stops)
    if s["matched"] < max(MIN_MATCHED * n, min(MIN_MATCHED_STOPS, n)) or s["mean_d"] > MAX_MEAN_DIST:
        return None, s, None, f"best variant fits poorly ({s['matched']}/{n} stops matched, mean {s['mean_d']:.0f} m)"
    second = next((o for ov, o in scored[1:] if ov is not v), None)
    margin = (second["misfit"] - s["misfit"]) if second else None
    return v, s, margin, ""


# --------------------------------------------------------------------------------------- feed
def assemble(part, matches, stops):
    """Polyline stretch through the matched stops; stops that lie beyond the line (before the first match,
    after the last one, or skipped in between) are joined in by straight pieces so no stop floats off the shape."""
    pts = []
    first_k = matches[0][0]
    pts += [(lon, lat) for _, lon, lat in stops[:first_k]]
    for i, (k, s0, _, _, _) in enumerate(matches):
        if i + 1 < len(matches):
            k2, s1 = matches[i + 1][0], matches[i + 1][1]
            seg = part.slice(s0, s1) if s1 > s0 else [part.slice(s0, s0)[0]]
            pts += seg[:-1] if i + 1 < len(matches) else seg
            pts += [(lon, lat) for _, lon, lat in stops[k + 1:k2]]
        else:
            pts.append(part.slice(s0, s0)[0])
    pts += [(lon, lat) for _, lon, lat in stops[matches[-1][0] + 1:]]
    out = []
    for p in pts:                                   # drop exact repeats
        if not out or abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9:
            out.append(p)
    return out


def trip_minutes(t):
    try:
        h, m, _ = str(t).split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def build(routes, trips, stop_times, stops, variants, service_daytype=None):
    """Returns (new_shapes_df, new_trips_df, report_df)."""
    service_daytype = VO.SERVICE_DAYTYPE if service_daytype is None else service_daytype
    by_code = defaultdict(list)
    for v in variants:
        by_code[v["code"]].append(v)
    code_of = dict(zip(routes["route_id"], routes["route_short_name"].astype(str).str.upper()))
    pos = {r.stop_id: (float(r.stop_lon), float(r.stop_lat)) for r in stops.itertuples()}

    st = stop_times[["trip_id", "stop_id", "stop_sequence", "departure_time"]].copy()
    st["seq"] = pd.to_numeric(st["stop_sequence"])
    st = st.sort_values(["trip_id", "seq"])
    per_trip = st.groupby("trip_id").agg(stops_=("stop_id", tuple), dep=("departure_time", "first"))
    t = trips.merge(per_trip, left_on="trip_id", right_index=True, how="left")
    t["daytype"] = t["service_id"].map(lambda s: DAYTYPES.get(service_daytype.get(str(s), "")))
    t["minute"] = t["dep"].map(trip_minutes)
    t["code"] = t["route_id"].map(code_of)

    shapes, new_shape_of, report = {}, {}, []
    for (rid, dt, d, seq), g in t[t["daytype"].notna() & t["stops_"].notna()].groupby(
            ["route_id", "daytype", "direction_id", "stops_"], dropna=False):
        code = code_of.get(rid, "")
        row = {"route_short_name": code, "daytype": dt, "direction_id": d, "n_trips": len(g), "n_stops": len(seq),
               "first_dep": min((m for m in g["minute"] if m is not None), default=None),
               "variant": "", "dia_vari": "", "matched": "", "mean_dist_m": "", "misfit": "", "window_share": "",
               "margin": "", "status": "kept old shape", "reason": ""}
        cand = by_code.get(code)
        if not cand and len(code) >= 3:       # feed code T57 vs rutas code T57A: same line number, letter differs
            cand = [v for c, vs in by_code.items() if c[:3] == code[:3] for v in vs]
            if cand:
                row["reason"] = "rutas code differs (sibling variants tried)"
        if not cand:
            row["reason"] = "route not in rutas"
            report.append(row)
            continue
        stp = [(sid, *pos[sid]) for sid in seq if sid in pos]
        if len(stp) != len(seq) or len(stp) < 2:
            row["reason"] = "stops without coordinates"
            report.append(row)
            continue
        sibling = row["reason"]
        v, s, margin, reason = choose_variant(cand, stp, list(g["minute"]), dt)
        if v is None:
            row["reason"] = reason
            report.append(row)
            continue
        m = s["matches"]
        sid_ = f"{code}-{v['fid']}-{seq[0]}-{seq[-1]}-{len(seq)}"
        if sid_ not in shapes:
            pts = assemble(s["part"], m, stp)
            if len(pts) >= 2:
                shapes[sid_] = pts
        if sid_ not in shapes:
            row["reason"] = "empty stretch"
            report.append(row)
            continue
        for tid in g["trip_id"]:
            new_shape_of[tid] = sid_
        row.update({"variant": v["variante"], "dia_vari": v["dia_vari"], "matched": f"{s['matched']}/{len(seq)}",
                    "mean_dist_m": round(s["mean_d"], 1), "misfit": s["misfit"],
                    "window_share": round(s["window_share"], 2), "margin": "" if margin is None else margin,
                    "status": "applied", "reason": ("matched on " + v["code"] + " (" + sibling + ")") if sibling and v["code"] != code else ""})
        report.append(row)

    rows = []
    for sid_, pts in shapes.items():
        cum, prev = 0.0, None
        for i, (lon, lat) in enumerate(pts, start=1):
            if prev is not None:
                cum += float(np.hypot((lon - prev[0]) * M_LON, (lat - prev[1]) * M_LAT))
            rows.append((sid_, f"{lat:.6f}", f"{lon:.6f}", i, round(cum, 1)))
            prev = (lon, lat)
    new_shapes = pd.DataFrame(rows, columns=["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence", "shape_dist_traveled"])
    nt = trips.copy()
    if "shape_id" not in nt.columns:
        nt["shape_id"] = ""
    nt["shape_id"] = [new_shape_of.get(tid, old) for tid, old in zip(nt["trip_id"], nt["shape_id"])]
    return new_shapes, nt, pd.DataFrame(report)


def merge_shapes(old_shapes, new_shapes, trips):
    """Old shapes still used by a trip stay; new ones are added; unreferenced ones go."""
    used = set(trips["shape_id"].dropna()) - {""}
    keep_old = old_shapes[old_shapes["shape_id"].isin(used - set(new_shapes["shape_id"]))] if old_shapes is not None else None
    parts = [p for p in (keep_old, new_shapes[new_shapes["shape_id"].isin(used)]) if p is not None and len(p)]
    return pd.concat(parts, ignore_index=True) if parts else new_shapes


def fetch_rutas(log=print):
    import arcgis_client as AC
    from gtfs_common import ROUTES_INFO_FEATURE_SERVICE_URL, ROUTES_INFO_ITEM_ID, ROUTES_INFO_LAYER_ID
    url = AC.resolve_feature_service_url(ROUTES_INFO_ITEM_ID, fallback_url=ROUTES_INFO_FEATURE_SERVICE_URL)
    data = AC.get_json(f"{url}/{ROUTES_INFO_LAYER_ID}/query", where="1=1", outFields="*", outSR=4326,
                       returnGeometry="true", resultRecordCount=2000)
    feats = data.get("features", [])
    log(f"  rutas: {len(feats)} polyline row(s)")
    return feats


def main(clean_dir=None, report_dir=None, features=None):
    from gtfs_common import CLEAN_DIR, FINAL_ZIP, REPORT_DIR
    clean_dir, report_dir = clean_dir or CLEAN_DIR, report_dir or REPORT_DIR
    read = lambda f: pd.read_csv(os.path.join(clean_dir, f), dtype=str, keep_default_na=False)
    try:
        features = features if features is not None else fetch_rutas()
    except Exception as e:
        print(f"rutas not reachable ({type(e).__name__}: {e}) - shapes stay as they are.")
        return None
    variants = variants_from_features(features)
    if len(variants) < 30:
        print(f"rutas gave only {len(variants)} usable variant(s) - shapes stay as they are.")
        return None
    routes, trips, stops = read("routes.txt"), read("trips.txt"), read("stops.txt")
    stop_times = pd.read_csv(os.path.join(clean_dir, "stop_times.txt"), dtype=str, keep_default_na=False,
                             usecols=["trip_id", "stop_id", "stop_sequence", "departure_time"])
    old = read("shapes.txt") if os.path.exists(os.path.join(clean_dir, "shapes.txt")) else None
    new_shapes, new_trips, rep = build(routes, trips, stop_times, stops, variants)
    os.makedirs(report_dir, exist_ok=True)
    rep.to_csv(os.path.join(report_dir, "shape_assignment.csv"), index=False)
    applied = rep[rep["status"] == "applied"]
    total_trips, applied_trips = int(rep["n_trips"].sum()), int(applied["n_trips"].sum())
    print(f"shapes from rutas: {len(applied)}/{len(rep)} stop lists matched ({applied_trips}/{total_trips} trips); "
          f"{new_shapes['shape_id'].nunique()} shape(s) written; old shapes kept where nothing fit")
    final = merge_shapes(old, new_shapes, new_trips)
    tmp = {f: os.path.join(clean_dir, f + ".tmp") for f in ("shapes.txt", "trips.txt")}
    final.to_csv(tmp["shapes.txt"], index=False, encoding="utf-8")          # both written before either replaces
    new_trips.to_csv(tmp["trips.txt"], index=False, encoding="utf-8")
    for f, t in tmp.items():
        os.replace(t, os.path.join(clean_dir, f))
    with zipfile.ZipFile(FINAL_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn in sorted(os.listdir(clean_dir)):
            if fn.endswith(".txt"):
                zf.write(os.path.join(clean_dir, fn), arcname=fn)
    print(f"rewrote {FINAL_ZIP}")
    return rep


if __name__ == "__main__":
    main()
