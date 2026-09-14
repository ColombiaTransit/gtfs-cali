"""
Exercises reconstruct.py's join logic against small, hand-built DataFrames
that mirror the real Metro Cali FeatureServer schema exactly (same field
names/types as returned by the live layers). No network access needed.

Run: python test_reconstruct.py
"""
import pandas as pd

import reconstruct as R

# ---- fabricate one route, one pattern, 3 stops, 2 segments, 2 runs ----

stops_raw = pd.DataFrame([
    {"OBJECTID": 1, "ID": 501, "GStopID": "S1", "GStopType": 0, "GStopParen": None,
     "ParentID": None, "GWheelchairBoarding": 1, "stop_lat": 3.45, "stop_lon": -76.53},
    {"OBJECTID": 2, "ID": 502, "GStopID": "S2", "GStopType": 0, "GStopParen": None,
     "ParentID": None, "GWheelchairBoarding": 1, "stop_lat": 3.46, "stop_lon": -76.52},
    {"OBJECTID": 3, "ID": 503, "GStopID": "S3", "GStopType": 0, "GStopParen": None,
     "ParentID": None, "GWheelchairBoarding": 0, "stop_lat": 3.47, "stop_lon": -76.51},
])

lines_raw = pd.DataFrame([
    {"ID": 9001, "GRouteID": "A01", "GRouteType": 3},
])

line_variants_raw = pd.DataFrame([
    {"ID": 7001, "LineID": 9001, "GDirectionID": 0, "GShapeID": "SHP1"},
])

# 2 segments -> 3 stops: S1->S2 (SqIdx 1), S2->S3 (SqIdx 2)
line_variant_elements_raw = pd.DataFrame([
    {"LineVarID": 7001, "SqIdx": 1, "FromStopID": 501, "ToStopID": 502,
     "_geom_path": [(3.45, -76.53), (3.455, -76.525), (3.46, -76.52)]},
    {"LineVarID": 7001, "SqIdx": 2, "FromStopID": 502, "ToStopID": 503,
     "_geom_path": [(3.46, -76.52), (3.465, -76.515), (3.47, -76.51)]},
])

calendars_raw = pd.DataFrame([
    {"ID": 3001, "GServiceID": "WK", "Monday": 1, "Tuesday": 1, "Wednesday": 1,
     "Thursday": 1, "Friday": 1, "Saturday": 0, "Sunday": 0,
     "StartDate": "20260101", "EndDate": "20260201"},
])

schedules_raw = pd.DataFrame([
    {"ID": 5001, "LineVarID": 7001},
])

runs_raw = pd.DataFrame([
    {"ID": 1, "ScheduleID": 5001, "StartRun": 6.0, "GTripID": "T1",
     "CalendarID": 3001, "GWheelchairAccessible": 1, "GBikesAllowed": 0},
    {"ID": 2, "ScheduleID": 5001, "StartRun": 7.0, "GTripID": "T2",
     "CalendarID": 3001, "GWheelchairAccessible": 1, "GBikesAllowed": 0},
])


def test_per_stop_alignment():
    # 3 ScheduleElements rows == 3 stops -> should detect "per_stop"
    schedule_elements_raw = pd.DataFrame([
        {"ScheduleID": 5001, "SqIdx": 1, "Arrival": 0.0, "Departure": 0.0},
        {"ScheduleID": 5001, "SqIdx": 2, "Arrival": 0.1667, "Departure": 0.1833},  # +10min/+11min
        {"ScheduleID": 5001, "SqIdx": 3, "Arrival": 0.35, "Departure": 0.35},       # +21min
    ])

    by_id, by_oid = R.stop_id_lookup(R.build_stops(stops_raw))
    sequences, geometries = R.build_stop_sequence_per_pattern(line_variant_elements_raw, by_id, by_oid)
    assert sequences[7001] == ["S1", "S2", "S3"], sequences

    routes = R.build_routes(lines_raw)
    lv_lookup = R.build_line_variant_lookup(line_variants_raw, dict(zip(routes["_internal_id"], routes["route_id"])))
    assert lv_lookup[7001] == {"route_id": "A01", "direction_id": 0, "shape_id": "SHP1"}

    alignment = R.detect_schedule_alignment(schedule_elements_raw, schedules_raw, sequences)
    assert alignment == "per_stop", alignment

    unit = R.detect_time_unit(runs_raw, schedule_elements_raw, schedules_raw)
    assert unit == "hours", unit

    calendar_df = R.build_calendar(calendars_raw)
    cal_lookup = R.calendar_id_lookup(calendar_df)

    trips, stop_times = R.build_trips_and_stop_times(
        runs_raw, schedules_raw, schedule_elements_raw, lv_lookup, sequences,
        cal_lookup, unit, alignment,
    )
    assert len(trips) == 2
    assert set(trips["trip_id"]) == {"T1", "T2"}
    assert trips.iloc[0]["route_id"] == "A01"
    assert trips.iloc[0]["service_id"] == "WK"

    t1_times = stop_times[stop_times["trip_id"] == "T1"].sort_values("stop_sequence")
    assert list(t1_times["stop_id"]) == ["S1", "S2", "S3"]
    assert list(t1_times["arrival_time"]) == ["06:00:00", "06:10:00", "06:21:00"]
    assert list(t1_times["departure_time"]) == ["06:00:00", "06:11:00", "06:21:00"]

    t2_times = stop_times[stop_times["trip_id"] == "T2"].sort_values("stop_sequence")
    assert list(t2_times["arrival_time"]) == ["07:00:00", "07:10:00", "07:21:00"]

    print("test_per_stop_alignment: PASS")


def test_per_segment_alignment():
    # only 2 ScheduleElements rows == n_stops - 1 -> should detect "per_segment"
    schedule_elements_raw = pd.DataFrame([
        {"ScheduleID": 5001, "SqIdx": 1, "Arrival": 0.1667, "Departure": 0.0},     # seg S1->S2: dep S1 @0, arr S2 @+10min
        {"ScheduleID": 5001, "SqIdx": 2, "Arrival": 0.35, "Departure": 0.1833},    # seg S2->S3: dep S2 @+11min, arr S3 @+21min
    ])

    by_id, by_oid = R.stop_id_lookup(R.build_stops(stops_raw))
    sequences, _ = R.build_stop_sequence_per_pattern(line_variant_elements_raw, by_id, by_oid)

    alignment = R.detect_schedule_alignment(schedule_elements_raw, schedules_raw, sequences)
    assert alignment == "per_segment", alignment

    unit = R.detect_time_unit(runs_raw, schedule_elements_raw, schedules_raw)
    assert unit == "hours", unit

    routes = R.build_routes(lines_raw)
    lv_lookup = R.build_line_variant_lookup(line_variants_raw, dict(zip(routes["_internal_id"], routes["route_id"])))
    calendar_df = R.build_calendar(calendars_raw)
    cal_lookup = R.calendar_id_lookup(calendar_df)

    trips, stop_times = R.build_trips_and_stop_times(
        runs_raw, schedules_raw, schedule_elements_raw, lv_lookup, sequences,
        cal_lookup, unit, alignment,
    )
    t1_times = stop_times[stop_times["trip_id"] == "T1"].sort_values("stop_sequence")
    assert list(t1_times["stop_id"]) == ["S1", "S2", "S3"]
    assert list(t1_times["arrival_time"]) == ["06:00:00", "06:10:00", "06:21:00"]
    assert list(t1_times["departure_time"]) == ["06:00:00", "06:11:00", "06:21:00"]
    print("test_per_segment_alignment: PASS")


def test_shapes_built_with_cumulative_distance():
    by_id, by_oid = R.stop_id_lookup(R.build_stops(stops_raw))
    sequences, geometries = R.build_stop_sequence_per_pattern(line_variant_elements_raw, by_id, by_oid)
    routes = R.build_routes(lines_raw)
    lv_lookup = R.build_line_variant_lookup(line_variants_raw, dict(zip(routes["_internal_id"], routes["route_id"])))
    shapes = R.build_shapes(lv_lookup, geometries)
    assert shapes is not None
    assert list(shapes["shape_id"].unique()) == ["SHP1"]
    assert list(shapes["shape_pt_sequence"]) == [1, 2, 3, 4, 5]  # dedup shared vertex at segment join
    assert shapes["shape_dist_traveled"].is_monotonic_increasing
    print("test_shapes_built_with_cumulative_distance: PASS")


def test_time_unit_detection_minutes_and_seconds():
    se_minutes = pd.DataFrame([
        {"ScheduleID": 5001, "SqIdx": 1, "Arrival": 0.0, "Departure": 0.0},
        {"ScheduleID": 5001, "SqIdx": 2, "Arrival": 10.0, "Departure": 11.0},
        {"ScheduleID": 5001, "SqIdx": 3, "Arrival": 21.0, "Departure": 21.0},
    ])
    runs_minutes = pd.DataFrame([
        {"ID": 1, "ScheduleID": 5001, "StartRun": 360.0, "GTripID": "T1", "CalendarID": 3001,
         "GWheelchairAccessible": 1, "GBikesAllowed": 0},  # 360 min = 6:00
    ])
    unit = R.detect_time_unit(runs_minutes, se_minutes, schedules_raw)
    assert unit == "minutes", unit

    se_seconds = pd.DataFrame([
        {"ScheduleID": 5001, "SqIdx": 1, "Arrival": 0.0, "Departure": 0.0},
        {"ScheduleID": 5001, "SqIdx": 2, "Arrival": 600.0, "Departure": 660.0},
        {"ScheduleID": 5001, "SqIdx": 3, "Arrival": 1260.0, "Departure": 1260.0},
    ])
    runs_seconds = pd.DataFrame([
        {"ID": 1, "ScheduleID": 5001, "StartRun": 21600.0, "GTripID": "T1", "CalendarID": 3001,
         "GWheelchairAccessible": 1, "GBikesAllowed": 0},  # 21600 s = 6:00
    ])
    unit = R.detect_time_unit(runs_seconds, se_seconds, schedules_raw)
    assert unit == "seconds", unit
    print("test_time_unit_detection_minutes_and_seconds: PASS")


if __name__ == "__main__":
    test_per_stop_alignment()
    test_per_segment_alignment()
    test_shapes_built_with_cumulative_distance()
    test_time_unit_detection_minutes_and_seconds()
    print("\nAll reconstruct.py tests passed.")
