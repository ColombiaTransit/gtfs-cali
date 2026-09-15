"""
shape_geometry_validate.py
===========================
Cross-validates our reconstructed shapes.txt (built from LineVariantElements
- see reconstruct.py) against the real route geometry in the separate
"rutas" dataset. Unlike the name-based enrichment in routes_enrich.py, this
checks the GEOMETRY itself: does our reconstructed path actually trace the
same real-world route as Metro Cali's own official line?

No network calls in here - see validate_routes_geometry.py for the
CLI/network wrapper.

--- Method -------------------------------------------------------------
For two polylines A and B, compute the "mean nearest-vertex distance": for
every vertex in A, find the distance to the nearest vertex in B (haversine),
then average. This is a coarse but robust proxy for path similarity -
if A and B trace the same road, most of A's vertices land within a few
meters of some vertex in B; if they diverge (different corridor, wrong
shape_id, bad join upstream), the distance grows quickly. It intentionally
avoids point-to-segment projection (and thus any risk of phantom connector
segments across a MultiLineString's disconnected parts) - nearest-VERTEX
distance only, which is accurate as long as vertices are reasonably dense
(true for both sources here: points roughly every 10-50m).

A route can have multiple direction/variant geometries in "rutas" sharing
one base route code (see routes_enrich.base_route_code). We compare our
shape against every candidate variant and keep the best (lowest mean
distance) match, since we don't know a priori which direction a given
shape_id corresponds to.
"""
import numpy as np
import pandas as pd

DIAGNOSTICS = []


def diag(msg):
    DIAGNOSTICS.append(msg)
    print(f"  [diagnostic] {msg}")


def haversine_m_vec(lat1, lon1, lat2, lon2):
    """Vectorized haversine distance in meters; broadcasts numpy arrays
    (used here with column/row vectors to build a full distance matrix)."""
    R = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def mean_nearest_vertex_distances(points_a, points_b):
    """For every point in points_a (list of (lat,lon)), the distance in
    meters to the nearest point in points_b. Returns a numpy array, one
    value per point in points_a."""
    if not points_a or not points_b:
        return np.array([])
    a_lat = np.array([p[0] for p in points_a])
    a_lon = np.array([p[1] for p in points_a])
    b_lat = np.array([p[0] for p in points_b])
    b_lon = np.array([p[1] for p in points_b])
    # (len(a), len(b)) distance matrix, vectorized
    dmat = haversine_m_vec(a_lat[:, None], a_lon[:, None], b_lat[None, :], b_lon[None, :])
    return dmat.min(axis=1)


def compare_shapes(shape_points, candidate_points):
    """Symmetric-ish comparison: distances from shape->candidate AND
    candidate->shape, so a candidate that's a short sub-segment of a much
    longer shape (or vice versa) doesn't score artificially well."""
    d_fwd = mean_nearest_vertex_distances(shape_points, candidate_points)
    d_rev = mean_nearest_vertex_distances(candidate_points, shape_points)
    combined = np.concatenate([d_fwd, d_rev]) if len(d_fwd) and len(d_rev) else np.array([])
    if combined.size == 0:
        return {"mean_m": None, "max_m": None, "p90_m": None, "n_points": 0}
    return {
        "mean_m": float(combined.mean()),
        "max_m": float(combined.max()),
        "p90_m": float(np.percentile(combined, 90)),
        "n_points": int(len(shape_points)),
    }


def find_best_match(shape_points, candidates):
    """candidates: {label: [(lat,lon), ...]}. Returns (best_label, metrics)
    or (None, None) if candidates is empty or shape_points is empty."""
    if not candidates or not shape_points:
        return None, None
    best_label, best_metrics = None, None
    for label, pts in candidates.items():
        metrics = compare_shapes(shape_points, pts)
        if metrics["mean_m"] is None:
            continue
        if best_metrics is None or metrics["mean_m"] < best_metrics["mean_m"]:
            best_label, best_metrics = label, metrics
    return best_label, best_metrics


# Thresholds for the OK/WARN/FAIL classification in the report. Chosen
# generously: GPS digitization noise and slightly different vertex
# placement between two independently-maintained datasets easily accounts
# for tens of meters even on a genuinely correct match.
OK_THRESHOLD_M = 30
WARN_THRESHOLD_M = 100


def classify(mean_m):
    if mean_m is None:
        return "NO_CANDIDATE"
    if mean_m <= OK_THRESHOLD_M:
        return "OK"
    if mean_m <= WARN_THRESHOLD_M:
        return "WARN"
    return "FAIL"


def validate_shapes(shape_groups, shape_id_to_route_id, route_variant_geometry):
    """
    shape_groups: {shape_id: [(lat,lon), ...]} - our reconstructed shapes,
        ordered by shape_pt_sequence.
    shape_id_to_route_id: {shape_id: route_id}
    route_variant_geometry: {base_route_code: {ruta_variant_label: [(lat,lon), ...]}}
        - from grouping the rutas dataset by base route code (see
        routes_enrich.build_route_variant_map for the grouping half; the
        CLI wrapper builds this dict combining that with geometry).

    Returns a DataFrame report, one row per shape_id.
    """
    rows = []
    for shape_id, pts in shape_groups.items():
        route_id = shape_id_to_route_id.get(shape_id)
        candidates = route_variant_geometry.get(route_id, {}) if route_id else {}
        if not candidates:
            rows.append({
                "shape_id": shape_id, "route_id": route_id, "matched_ruta": None,
                "mean_m": None, "max_m": None, "p90_m": None,
                "n_shape_points": len(pts), "status": "NO_CANDIDATE",
            })
            continue
        best_label, metrics = find_best_match(pts, candidates)
        status = classify(metrics["mean_m"] if metrics else None)
        rows.append({
            "shape_id": shape_id, "route_id": route_id, "matched_ruta": best_label,
            "mean_m": metrics["mean_m"] if metrics else None,
            "max_m": metrics["max_m"] if metrics else None,
            "p90_m": metrics["p90_m"] if metrics else None,
            "n_shape_points": len(pts), "status": status,
        })

    report = pd.DataFrame(rows)
    if len(report):
        counts = report["status"].value_counts().to_dict()
        diag(f"shape geometry validation: {counts}")
    return report
