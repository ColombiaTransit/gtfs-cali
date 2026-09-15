"""
Tests shape_geometry_validate.py's nearest-vertex distance metric and
best-candidate matching against synthetic polylines with known offsets.

Run: python test_shape_geometry_validate.py
"""
import shape_geometry_validate as SGV


def straight_line(lat0, lon0, lat1, lon1, n=20):
    return [(lat0 + (lat1 - lat0) * i / (n - 1), lon0 + (lon1 - lon0) * i / (n - 1)) for i in range(n)]


def offset_line(line, lat_offset_deg):
    return [(lat + lat_offset_deg, lon) for lat, lon in line]


def test_identical_lines_score_near_zero():
    line = straight_line(3.40, -76.53, 3.45, -76.50)
    metrics = SGV.compare_shapes(line, line)
    assert metrics["mean_m"] < 1.0, metrics
    assert metrics["max_m"] < 1.0, metrics
    print(f"test_identical_lines_score_near_zero: PASS (mean={metrics['mean_m']:.3f}m)")


def test_offset_line_scores_proportionally_to_offset():
    line = straight_line(3.40, -76.53, 3.45, -76.50)
    # ~0.0009 deg latitude ~= 100m
    shifted = offset_line(line, 0.0009)
    metrics = SGV.compare_shapes(line, shifted)
    assert 80 < metrics["mean_m"] < 120, metrics
    print(f"test_offset_line_scores_proportionally_to_offset: PASS (mean={metrics['mean_m']:.1f}m)")


def test_find_best_match_picks_closest_candidate():
    shape = straight_line(3.40, -76.53, 3.45, -76.50)
    candidates = {
        "A01A": offset_line(shape, 0.0009),   # ~100m off
        "A01B": offset_line(shape, 0.00005),  # ~5.5m off - the real match
        "A02":  straight_line(3.30, -76.60, 3.32, -76.58),  # totally different route
    }
    label, metrics = SGV.find_best_match(shape, candidates)
    assert label == "A01B", label
    assert metrics["mean_m"] < 30, metrics
    print(f"test_find_best_match_picks_closest_candidate: PASS (picked {label}, mean={metrics['mean_m']:.1f}m)")


def test_classify_thresholds():
    assert SGV.classify(None) == "NO_CANDIDATE"
    assert SGV.classify(5) == "OK"
    assert SGV.classify(30) == "OK"
    assert SGV.classify(31) == "WARN"
    assert SGV.classify(100) == "WARN"
    assert SGV.classify(101) == "FAIL"
    print("test_classify_thresholds: PASS")


def test_validate_shapes_end_to_end():
    good_shape = straight_line(3.40, -76.53, 3.45, -76.50)
    bad_shape = straight_line(3.10, -76.90, 3.12, -76.88)  # nowhere near any candidate

    shape_groups = {
        "S1": offset_line(good_shape, 0.00005),  # near-perfect match to A01B
        "S2": bad_shape,
        "S3": [(1.0, 1.0)],  # valid points but no route mapping at all
    }
    shape_id_to_route_id = {"S1": "A01", "S2": "A01"}  # S3 deliberately unmapped
    route_variant_geometry = {
        "A01": {
            "A01A": offset_line(good_shape, 0.0009),
            "A01B": good_shape,
        }
    }

    report = SGV.validate_shapes(shape_groups, shape_id_to_route_id, route_variant_geometry)
    row_s1 = report[report["shape_id"] == "S1"].iloc[0]
    row_s2 = report[report["shape_id"] == "S2"].iloc[0]
    row_s3 = report[report["shape_id"] == "S3"].iloc[0]

    assert row_s1["status"] == "OK", row_s1.to_dict()
    assert row_s1["matched_ruta"] == "A01B", row_s1.to_dict()
    assert row_s2["status"] == "FAIL", row_s2.to_dict()
    assert row_s3["status"] == "NO_CANDIDATE", row_s3.to_dict()
    print("test_validate_shapes_end_to_end: PASS")


if __name__ == "__main__":
    test_identical_lines_score_near_zero()
    test_offset_line_scores_proportionally_to_offset()
    test_find_best_match_picks_closest_candidate()
    test_classify_thresholds()
    test_validate_shapes_end_to_end()
    print("\nAll shape_geometry_validate.py tests passed.")
