import pandas as pd
import variant_report as V


def _feed():
    routes = pd.DataFrame({"route_id": ["r1"], "route_short_name": ["P82"]})
    stops = pd.DataFrame({"stop_id": list("ABCDEF"), "stop_name": ["Menga", "Cl 44", "Cl 52 a", "Cl 52 b", "Calipso", "Acuaparque"]})
    trips, st = [], []

    def trip(tid, service, seq, dep):
        trips.append({"trip_id": tid, "route_id": "r1", "service_id": service, "direction_id": "0"})
        for i, s in enumerate(seq, start=1):
            st.append({"trip_id": tid, "stop_id": s, "stop_sequence": str(i), "departure_time": f"{dep}:00" if i == 1 else "00:00:00"})
    normal, cv = list("ABEF")[:3] + ["F"], list("ACDE")           # Normal via Cl 44; Ciclovia via Cl 52
    normal = ["A", "B", "E"]
    for k, dep in enumerate(["06:00", "15:00", "16:00"]):
        trip(f"n{k}", "8099043", normal, dep)
    for k, dep in enumerate(["07:00", "10:30", "13:45"]):
        trip(f"c{k}", "8099043", cv, dep)
    trip("w0", "8099041", normal, "08:00")                      # weekday: one pattern only -> not reported
    trip("x0", "unknown", cv, "09:00")                          # unmapped service ids are ignored
    return routes, pd.DataFrame(trips), pd.DataFrame(st), stops


def test_sunday_patterns_and_window():
    routes, trips, st, stops = _feed()
    rep = V.build_report(routes, trips, st, stops, windows={"P82": (420, 840)})
    assert set(rep["daytype"]) == {"DOM_FEST"} and len(rep) == 2
    dom, var = rep.iloc[0], rep.iloc[1]
    assert dom["is_dominant"] and dom["n_trips"] == 3 and dom["n_stops"] == 3 and dom["in_window_pct"] == 0
    assert var["n_trips"] == 3 and var["first_dep"] == "07:00" and var["last_dep"] == "13:45" and var["in_window_pct"] == 100
    assert var["added_vs_dominant"] == "Cl 52 a | Cl 52 b" and var["removed_vs_dominant"] == "Cl 44"
    assert var["ciclovia_window"] == "07:00-14:00"


def test_ciclovia_windows_from_listado():
    rows = [{"route_code": "P82", "variante": "Domingo Ciclovía - X", "dom_fest": "7:00-14:00"},
            {"route_code": "P82", "variante": "Domingo Normal - X", "dom_fest": "6:00-7:00 / 14:00-22:00"},
            {"route_code": "A02", "variante": "Hábil Normal - Y", "dom_fest": "NO OPERA"}]
    assert V.ciclovia_windows(rows) == {"P82": (420, 840)}


if __name__ == "__main__":
    test_sunday_patterns_and_window()
    test_ciclovia_windows_from_listado()
    print("All test_variant_report.py tests passed.")
