"""
routes_enrich.py
=================
Cross-references our reconstructed routes.txt (which has route_id and
route_type but NO name fields at all - see reconstruct.py) against Metro
Cali's separate "rutas" open-data layer, which carries real route names
and rich schedule/service metadata.

No network calls in here - see enrich_routes.py for the CLI/network wrapper.

--- Source schema (confirmed live) -----------------------------------------
RUTA        - route/variant code, e.g. "A01A", "A01B", "A02", "A11B".
              Base route code + an OPTIONAL trailing letter for direction/
              variant (A01A/A01B are the two directions of route A01; A02
              has only one variant so carries no suffix).
NOMBRE      - human-readable description, e.g.
              "ESTACIÓN SAN BOSCO - CAM - CENTRO". Populated on every row.
              This is what becomes route_long_name.
SERVICIO    - e.g. "ALIMENTADOR" (feeder), "TRONCAL", etc.
TIPOLOGIA   - vehicle type, e.g. "PADRON", "COMPLEMENTARIO".
HABIL / SABADO / DOM_FEST - operating-hours windows per day type.
(plus LineString/MultiLineString geometry - real route shapes, not used
 for name matching here, but worth cross-checking against our
 reconstructed shapes.txt separately if their quality is ever in doubt.)

--- Matching strategy -------------------------------------------------------
1. Derive a base route code from RUTA by stripping a single trailing
   uppercase letter IF doing so still leaves the code ending in a digit
   (handles "A01A" -> "A01", "A11B" -> "A11", while leaving "A02" alone
   since it has no suffix to strip).
2. Match base codes against route_id, tracking coverage the same way
   stops_enrich.py does (only trust the match if it clears a reasonable
   coverage bar).
3. Multiple RUTA rows can share a base route_id (one per direction/variant).
   For route_long_name, prefer the row whose RUTA equals the base code
   exactly (the "primary" variant with no suffix); otherwise take the
   first variant found, and log when different variants disagree on NOMBRE
   so a human can sanity-check which one was picked.
"""
import re
from collections import defaultdict

import numpy as np
import pandas as pd

DIAGNOSTICS = []


def diag(msg):
    DIAGNOSTICS.append(msg)
    print(f"  [diagnostic] {msg}")


def haversine_m_vec(lat1, lon1, lat2, lon2):
    """Vectorized haversine distance in meters; broadcasts numpy arrays."""
    R = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def base_route_code(ruta):
    """'A01A' -> 'A01', 'A11B' -> 'A11', 'A02' -> 'A02' (no suffix to strip)."""
    if pd.isna(ruta):
        return None
    s = str(ruta).strip()
    m = re.match(r"^(.*\d)([A-Z])$", s)
    return m.group(1) if m else s


# VARIANTE values seen in the live data beyond "NORMAL": "CICLOVIA" (a
# detour that ONLY operates on Sundays when Cali closes streets for
# ciclovía) and "DESVIO" (an hour-of-day detour, e.g. during specific
# service windows). Both can share the exact same RUTA code as the
# standing "NORMAL" route, differing only in geometry/DIA_TIPO - so they
# must be filtered out before any ID or geometry matching, or a special-
# occasion detour can get picked as if it were the everyday route. GTFS
# has no clean way to represent "this route takes a different path during
# ciclovía" without calendar-based trip splitting, which is out of scope
# here; we always want the NORMAL variant for routes.txt/shapes.txt
# identity matching.
NORMAL_VARIANT_VALUE = "NORMAL"


def filter_to_normal_variant(ext_df, variante_col="VARIANTE"):
    """Returns ext_df restricted to VARIANTE == 'NORMAL' (case-insensitive).
    Rows with no VARIANTE column, or a blank/missing value, are kept as-is
    (defensive: don't silently drop everything if the schema changes) -
    only rows that EXPLICITLY name a non-NORMAL variant (CICLOVIA, DESVIO,
    etc.) are excluded."""
    if variante_col not in ext_df.columns:
        diag(f"no '{variante_col}' column found - cannot filter special-occasion "
             f"route variants (CICLOVIA/DESVIO), using all rows as-is")
        return ext_df

    col = ext_df[variante_col]
    is_missing = col.isna()
    norm = col.astype(str).str.strip().str.upper()
    is_normal_or_blank = is_missing | norm.isin(["", "NAN", "NONE", NORMAL_VARIANT_VALUE])
    excluded = ext_df[~is_normal_or_blank]
    if len(excluded):
        counts = excluded[variante_col].value_counts().to_dict()
        diag(f"excluded {len(excluded)} special-occasion route variant row(s) "
             f"before matching (not the standing/everyday route): {counts}")
    return ext_df[is_normal_or_blank]


def build_route_variant_map(ext_df):
    """Group ext_df rows by base route code. Returns
    {base_code: [(RUTA, row_index), ...]}, and logs how many distinct base
    codes were derived from how many source rows."""
    groups = defaultdict(list)
    for idx, row in ext_df.iterrows():
        base = base_route_code(row.get("RUTA"))
        if base:
            groups[base].append((str(row.get("RUTA")).strip(), idx))
    diag(f"rutas: {len(ext_df)} source row(s) grouped into {len(groups)} "
         f"distinct base route code(s)")
    return groups


def pick_best_variant(base_code, variants, ext_df, name_col):
    """Among rows sharing a base route code, prefer the one whose RUTA
    equals the base code exactly (no direction suffix); else take the
    first. Logs a note if variants disagree on the name text."""
    exact = [idx for ruta, idx in variants if ruta == base_code]
    chosen_idx = exact[0] if exact else variants[0][1]

    names = {str(ext_df.loc[idx, name_col]).strip() for _, idx in variants
             if pd.notna(ext_df.loc[idx, name_col])}
    if len(names) > 1:
        diag(f"route '{base_code}': {len(variants)} variant(s) disagree on "
             f"name ({names}) - using the one from RUTA='{ext_df.loc[chosen_idx, 'RUTA']}'")
    return chosen_idx


def best_id_match(our_routes_df, route_groups):
    """Compare our route_id values against the derived base route codes.
    Returns (coverage_fraction, {route_id: chosen_ext_row_index})."""
    our_ids = list(our_routes_df["route_id"].astype(str))
    matches = {}
    for rid in our_ids:
        if rid in route_groups:
            matches[rid] = route_groups[rid]
    coverage = len(matches) / len(our_ids) if our_ids else 0
    diag(f"route_id coverage via RUTA base-code match: {len(matches)}/{len(our_ids)} "
         f"({coverage:.0%})")
    return coverage, matches


# --------------------------------------------------------------------------
# Geometry-based matching - the real fallback for this feed
# --------------------------------------------------------------------------
# Confirmed against live Metro Cali sources (metrocali.gov.co/newmioapp and
# public communications): real MIO route codes are always letter-prefixed
# (T## troncal, P## pretroncal, A## alimentadora, e.g. "A01", "T14",
# "A35A"). Our reconstructed route_id comes from the GTFS FeatureServer's
# Lines.GRouteID field, which turned out to be a PLAIN INTEGER internal
# scheduling-system ID (e.g. "112") - a completely different ID space with
# no shared key to "rutas". ID-based matching (best_id_match above)
# reliably scores ~0% for this feed - it's kept for portability (a future
# vigencia might restore a shared ID scheme) but geometry matching below is
# the one that actually works here: compare each route's real path against
# every rutas geometry and take the closest, the same principle as
# validate_routes_geometry.py's shape-level QA check, just used here to
# find identity rather than to confirm it.

ROUTE_MATCH_TRUST_THRESHOLD_M = 150  # more lenient than shape QA's OK
# threshold (30m) since this is now a matching/identification mechanism,
# not a "did the pipeline reproduce this shape exactly" check - some
# genuine schedule/digitization drift between two independently maintained
# sources is expected even for a correct match.
CENTROID_PREFILTER_KM = 5.0  # skip full-geometry comparison for candidates
# whose centroid is obviously nowhere near the route (cheap first pass)


def route_centroid(points):
    lat = sum(p[0] for p in points) / len(points)
    lon = sum(p[1] for p in points) / len(points)
    return lat, lon


def build_route_representative_shapes(shapes_df, trips_df):
    """One representative shape per route_id - the most-detailed (most
    points) shape used by any trip on that route, as a stand-in for "the
    route's real path". Returns {route_id: [(lat, lon), ...]}."""
    if "shape_id" not in trips_df.columns:
        return {}
    pairs = trips_df.dropna(subset=["shape_id"])[["route_id", "shape_id"]].drop_duplicates()

    shape_points = {}
    for shape_id, g in shapes_df.groupby("shape_id"):
        g = g.sort_values("shape_pt_sequence")
        shape_points[shape_id] = list(zip(g["shape_pt_lat"], g["shape_pt_lon"]))

    best_by_route = {}
    for _, row in pairs.iterrows():
        route_id, shape_id = row["route_id"], row["shape_id"]
        pts = shape_points.get(shape_id)
        if not pts:
            continue
        if route_id not in best_by_route or len(pts) > len(best_by_route[route_id]):
            best_by_route[route_id] = pts
    return best_by_route


def match_routes_by_geometry(route_shapes, ext_df, geom_col="_geom_path",
                              prefilter_km=CENTROID_PREFILTER_KM):
    """For each route_id -> representative shape, find the closest rutas
    row by nearest-vertex distance (see shape_geometry_validate.compare_shapes).
    Returns {route_id: {"ext_idx", "mean_m", "ruta"}}."""
    import shape_geometry_validate as SGV  # local import: avoids a hard
    # dependency for callers that only need ID-based matching

    candidates = []
    for idx, row in ext_df.iterrows():
        pts = row.get(geom_col)
        if pts:
            candidates.append((idx, pts, route_centroid(pts)))

    results = {}
    for route_id, shape_pts in route_shapes.items():
        r_centroid = route_centroid(shape_pts)
        nearby = [
            (idx, pts) for idx, pts, c_centroid in candidates
            if haversine_m_vec(r_centroid[0], r_centroid[1], c_centroid[0], c_centroid[1]) <= prefilter_km * 1000
        ]
        if not nearby:
            continue
        best_idx, best_metrics = SGV.find_best_match(shape_pts, {idx: pts for idx, pts in nearby})
        if best_idx is None:
            continue
        results[route_id] = {
            "ext_idx": best_idx, "mean_m": best_metrics["mean_m"],
            "ruta": str(ext_df.loc[best_idx, "RUTA"]).strip(),
        }

    if route_shapes:
        n_trusted = sum(1 for r in results.values() if r["mean_m"] <= ROUTE_MATCH_TRUST_THRESHOLD_M)
        diag(f"geometry match: {len(results)}/{len(route_shapes)} route(s) found a "
             f"nearby candidate; {n_trusted} within the {ROUTE_MATCH_TRUST_THRESHOLD_M}m "
             f"trust threshold")
    return results


def enrich_routes(our_routes_df, ext_df, name_col="NOMBRE", shapes_df=None, trips_df=None):
    """Returns (enriched_df, stats_dict). enriched_df is our_routes_df with
    route_long_name (and, for geometry-matched routes, a corrected
    route_short_name using the real letter-coded RUTA) filled in wherever a
    match was found (unchanged elsewhere - fix.py's placeholder logic
    still handles those).

    Tries ID-based matching first (best_id_match); if that doesn't clear
    the coverage bar AND shapes_df/trips_df were provided, falls back to
    geometry-based matching (match_routes_by_geometry) - see that
    function's docstring for why this feed specifically needs it.

    Special-occasion route variants (VARIANTE == CICLOVIA/DESVIO/etc, see
    filter_to_normal_variant) are excluded before any matching, since they
    can share a RUTA code with the everyday route but describe a
    different, occasional path."""
    ext_df = filter_to_normal_variant(ext_df)

    if name_col not in ext_df.columns:
        diag(f"WARNING: expected name column '{name_col}' not found in "
             f"rutas data (columns: {list(ext_df.columns)}). Cannot enrich "
             f"route_long_name.")
        return our_routes_df.copy(), {"matched": 0, "total": len(our_routes_df), "method": None}

    route_groups = build_route_variant_map(ext_df)
    coverage, id_matches = best_id_match(our_routes_df, route_groups)

    enriched = our_routes_df.copy()
    if "route_long_name" not in enriched.columns:
        enriched["route_long_name"] = pd.NA
    total = len(our_routes_df)
    matched_count = 0
    method = None

    if coverage >= 0.5:
        method = "id"
        for route_id, variants in id_matches.items():
            chosen_idx = pick_best_variant(route_id, variants, ext_df, name_col)
            name = ext_df.loc[chosen_idx, name_col]
            if pd.notna(name) and str(name).strip():
                enriched.loc[enriched["route_id"].astype(str) == route_id, "route_long_name"] = str(name).strip()
                matched_count += 1
    else:
        diag(f"ID coverage too low ({coverage:.0%}) to trust - route naming "
             f"convention differs from the expected letter-coded pattern for "
             f"this feed's route_id values")
        if shapes_df is None or trips_df is None or "_geom_path" not in ext_df.columns:
            diag("No shape/trip data (or no rutas geometry) provided for a "
                 "geometry-based fallback - cannot enrich route_long_name.")
            return our_routes_df.copy(), {"matched": 0, "total": total, "method": None}

        method = "geometry"
        route_shapes = build_route_representative_shapes(shapes_df, trips_df)
        geo_matches = match_routes_by_geometry(route_shapes, ext_df)
        for route_id, info in geo_matches.items():
            if info["mean_m"] > ROUTE_MATCH_TRUST_THRESHOLD_M:
                continue
            ext_idx = info["ext_idx"]
            name = ext_df.loc[ext_idx, name_col]
            short = base_route_code(info["ruta"])
            mask = enriched["route_id"].astype(str) == str(route_id)
            if pd.notna(name) and str(name).strip():
                enriched.loc[mask, "route_long_name"] = str(name).strip()
                matched_count += 1
            if short:
                enriched.loc[mask, "route_short_name"] = short

    diag(f"TOTAL: {matched_count}/{total} route(s) enriched with a real "
         f"long name via {method or 'no'} matching "
         f"({matched_count/total:.0%})" if total else "TOTAL: 0 routes to enrich")

    return enriched, {"matched": matched_count, "total": total, "method": method}
