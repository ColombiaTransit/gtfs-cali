import pandas as pd
import shapes_from_rutas as S

LON0, LAT0 = -76.5000, 3.4300


def loop(detour=False):
    """Out along lon -76.5000 (north), back along lon -76.4990 (about 110 m east)."""
    out = [(LON0, LAT0 + i * 0.001) for i in range(0, 21)]
    back_lon = -76.4990 + (0.0005 if detour else 0.0)
    back = [(back_lon, LAT0 + 0.020 - i * 0.001) for i in range(0, 21)]
    return out + back


def feature(code, fid, variante, dia_vari, habil, sab, dom, lonlat):
    return {"attributes": {"RUTA": code, "FID": fid, "VARIANTE": variante, "DIA_VARI": dia_vari,
                           "HABIL": habil, "SABADO": sab, "DOM_FEST": dom},
            "geometry": {"paths": [[list(p) for p in lonlat]]}}


def test_parse_ranges_and_windows():
    assert S.parse_ranges(" 6:00-7:00 / 14:00-22:00") == [(360, 420), (840, 1320)]
    assert S.parse_ranges(" NO OPERA") is None and S.parse_ranges("") is None and S.parse_ranges(None) is None
    assert S.in_ranges(415, [(420, 840)]) and not S.in_ranges(400, [(420, 840)]) and S.in_ranges(840, [(420, 840)])


def stops_on(lon, lats):
    return [(f"s{lon}_{i}", lon, lat) for i, lat in enumerate(lats)]


def test_stops_in_order_pick_the_right_direction():
    line = S.Line(loop())
    north = stops_on(-76.5000, [LAT0 + 0.0021, LAT0 + 0.0052, LAT0 + 0.0093, LAT0 + 0.0154])
    south = stops_on(-76.4990, [LAT0 + 0.0152, LAT0 + 0.0091, LAT0 + 0.0053, LAT0 + 0.0022])
    m, d = S.match_stops(line, north)
    assert [k for k, *_ in m] == [0, 1, 2, 3] and d < 5 and m[0][1] < m[-1][1] < line.length / 2 + 50
    m2, d2 = S.match_stops(line, south)
    assert [k for k, *_ in m2] == [0, 1, 2, 3] and d2 < 5 and m2[0][1] > line.length / 2
    pts = line.slice(m2[0][1], m2[-1][1])
    assert all(abs(p[0] + 76.4990) < 1e-6 for p in pts) and pts[0][1] > pts[-1][1]    # runs south on the way back


def test_unmatched_end_stops_are_joined_by_a_straight_piece():
    line = S.Line(loop())
    stops = stops_on(-76.5000, [LAT0 + 0.0021, LAT0 + 0.0052, LAT0 + 0.0093]) + [("far", -76.5000, LAT0 + 0.0250)]
    stops = [("lead", -76.5000, LAT0 - 0.0100)] + stops
    m, _ = S.match_stops(line, stops)
    assert [k for k, *_ in m] == [1, 2, 3]
    pts = S.assemble(line, m, stops)
    assert pts[0] == (-76.5000, LAT0 - 0.0100)


def mini_feed(dep_minutes, stops_lonlat_lats, lon, service="8099043", direction="0"):
    routes = pd.DataFrame({"route_id": ["r1"], "route_short_name": ["P82"]})
    stops, trips, st = {}, [], []
    for i, lat in enumerate(stops_lonlat_lats):
        stops[f"s{i}"] = (lon, lat)
    for k, m in enumerate(dep_minutes):
        tid = f"t{k}"
        trips.append({"trip_id": tid, "route_id": "r1", "service_id": service, "direction_id": direction, "shape_id": "old"})
        for i in range(len(stops)):
            st.append({"trip_id": tid, "stop_id": f"s{i}", "stop_sequence": str(i + 1),
                       "departure_time": f"{(m + i) // 60:02d}:{(m + i) % 60:02d}:00"})
    sdf = pd.DataFrame([{"stop_id": k, "stop_lon": str(v[0]), "stop_lat": str(v[1])} for k, v in stops.items()])
    return routes, pd.DataFrame(trips), pd.DataFrame(st), sdf


def test_variant_by_hours_and_misfit():
    normal = feature("P82", 1, "NORMAL", "DN", " NO OPERA", " NO OPERA", " 6:00-7:00 / 14:00-22:00", loop())
    cicl = feature("P82", 2, "CICLOVIA", "DCV", " NO OPERA", " NO OPERA", " 7:00-14:00", loop())
    variants = S.variants_from_features([normal, cicl])
    lats = [LAT0 + 0.0021, LAT0 + 0.0052, LAT0 + 0.0093, LAT0 + 0.0154]
    routes, trips, st, sdf = mini_feed([8 * 60, 10 * 60, 12 * 60], lats, -76.5000)
    ns, nt, rep = S.build(routes, trips, st, sdf, variants, service_daytype={"8099043": "DOM_FEST"})
    assert list(rep["variant"]) == ["CICLOVIA"] and rep.iloc[0]["status"] == "applied"      # hours decide: identical geometry
    routes, trips, st, sdf = mini_feed([15 * 60, 17 * 60], lats, -76.5000)
    ns, nt, rep = S.build(routes, trips, st, sdf, variants, service_daytype={"8099043": "DOM_FEST"})
    assert list(rep["variant"]) == ["NORMAL"]
    # a different alignment on the way back beats the hours: the stops lie on the detour only
    detour = feature("P82", 3, "CICLOVIA", "DCV", " NO OPERA", " NO OPERA", " 7:00-14:00", loop(detour=True))
    back_lats = [LAT0 + 0.0152, LAT0 + 0.0091, LAT0 + 0.0053, LAT0 + 0.0022]
    routes, trips, st, sdf = mini_feed([15 * 60], back_lats, -76.4985, direction="1")
    ns, nt, rep = S.build(routes, trips, st, sdf, S.variants_from_features([normal, detour]), service_daytype={"8099043": "DOM_FEST"})
    assert list(rep["variant"]) == ["CICLOVIA"] and rep.iloc[0]["misfit"] == 0


def test_build_assigns_shapes_and_keeps_old_ones():
    normal = feature("P82", 1, "NORMAL", "HN", " 5:00-23:00", " 5:00-23:00", " NO OPERA", loop())
    variants = S.variants_from_features([normal])
    lats = [LAT0 + 0.0021, LAT0 + 0.0052, LAT0 + 0.0093, LAT0 + 0.0154]
    routes, trips, st, sdf = mini_feed([8 * 60, 9 * 60], lats, -76.5000, service="8099041")
    other = trips.iloc[:1].assign(trip_id="x1", route_id="r2")           # route missing in rutas keeps its shape
    routes = pd.concat([routes, pd.DataFrame({"route_id": ["r2"], "route_short_name": ["Z99"]})])
    st = pd.concat([st, st[st.trip_id == "t0"].assign(trip_id="x1")])
    trips = pd.concat([trips, other], ignore_index=True)
    ns, nt, rep = S.build(routes, trips, st, sdf, variants, service_daytype={"8099041": "HABIL"})
    t = nt.set_index("trip_id")
    assert t.loc["t0", "shape_id"] == t.loc["t1", "shape_id"] != "old" and t.loc["x1", "shape_id"] == "old"
    assert set(rep["status"]) == {"applied", "kept old shape"}
    assert ns.shape_id.nunique() == 1 and (ns.groupby("shape_id").shape_dist_traveled.apply(lambda s: s.is_monotonic_increasing)).all()
    old = pd.DataFrame({"shape_id": ["old", "unused"], "shape_pt_lat": ["3.4", "3.4"], "shape_pt_lon": ["-76.5", "-76.5"],
                        "shape_pt_sequence": ["1", "1"], "shape_dist_traveled": ["0", "0"]})
    final = S.merge_shapes(old, ns, nt)
    assert set(final.shape_id) == {"old", t.loc["t0", "shape_id"]}        # unused old shape dropped, used one kept


def test_sibling_route_code_is_tried():
    normal = feature("T57A", 1, "NORMAL", "HN", " 5:00-23:00", " 5:00-23:00", " NO OPERA", loop())
    lats = [LAT0 + 0.0021, LAT0 + 0.0052, LAT0 + 0.0093, LAT0 + 0.0154]
    routes, trips, st, sdf = mini_feed([8 * 60], lats, -76.5000, service="8099041")
    routes["route_short_name"] = "T57"
    ns, nt, rep = S.build(routes, trips, st, sdf, S.variants_from_features([normal]), service_daytype={"8099041": "HABIL"})
    assert rep.iloc[0]["status"] == "applied" and "T57A" in rep.iloc[0]["reason"]


def test_rutas_unreachable_keeps_the_feed(tmp_path=None):
    import tempfile
    d = tempfile.mkdtemp()
    assert S.main(clean_dir=d, report_dir=d, features=[]) is None       # too few variants: nothing is touched



def test_split_rounds_and_siur_variants():
    one = loop() + [(LON0, LAT0)]                         # closed round
    two = one + one[1:]                                   # siur chains the round twice
    rounds = S.split_rounds(two)
    assert len(rounds) == 2
    v = S.variants_from_siur("X1", one)
    assert v and all(x["code"] == "X1" and x["variante"] == "SIUR" for x in v)


def test_build_uses_siur_when_rutas_has_no_line():
    routes = pd.DataFrame({"route_id": ["r1"], "route_short_name": ["X1"]})
    lats = [LAT0 + 0.0021, LAT0 + 0.0052, LAT0 + 0.0093, LAT0 + 0.0154]
    stops = pd.DataFrame({"stop_id": [f"s{i}" for i in range(4)], "stop_lon": [-76.5] * 4, "stop_lat": lats})
    trips = pd.DataFrame({"trip_id": ["t1"], "route_id": ["r1"], "service_id": ["8099041"], "direction_id": ["0"]})
    stop_times = pd.DataFrame({"trip_id": ["t1"] * 4, "stop_id": [f"s{i}" for i in range(4)],
                               "stop_sequence": ["1", "2", "3", "4"], "departure_time": ["08:00:00"] * 4})
    extra = S.variants_from_siur("X1", loop())
    _, new_trips, rep = S.build(routes, trips, stop_times, stops, [], fallback=extra)
    assert rep.iloc[0]["status"] == "applied" and rep.iloc[0]["reason"] == "siur fallback"
    assert new_trips.iloc[0]["shape_id"]
    _, _, rep2 = S.build(routes, trips, stop_times, stops, [])
    assert rep2.iloc[0]["status"] != "applied"


if __name__ == "__main__":
    test_parse_ranges_and_windows()
    test_stops_in_order_pick_the_right_direction()
    test_unmatched_end_stops_are_joined_by_a_straight_piece()
    test_variant_by_hours_and_misfit()
    test_build_assigns_shapes_and_keeps_old_ones()
    test_sibling_route_code_is_tried()
    test_rutas_unreachable_keeps_the_feed()
    test_split_rounds_and_siur_variants()
    test_build_uses_siur_when_rutas_has_no_line()
    print("All test_shapes_from_rutas.py tests passed.")
