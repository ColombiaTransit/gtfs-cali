import pandas as pd
import diagnose_service_daytypes as D

PDF = {"A11": {"HABIL": (300, 1380), "SABADO": (300, 840), "DOM_FEST": None},
       "A02": {"HABIL": (300, 1380), "SABADO": (300, 1380), "DOM_FEST": (360, 1320)},
       "A01A": {"HABIL": (300, 1200), "SABADO": (300, 1200), "DOM_FEST": None}}

def mk(sid, route, start, end, n=5):
    return [(f"{sid}-{route}-{i}", route, sid, start, end) for i in range(n)]

def build():
    rows = (mk("1", "A11", 300, 1380, 20) + mk("1", "A02", 300, 1380, 20) + mk("1", "A01A", 300, 1200, 20) +
            mk("2", "A11", 300, 840, 10) + mk("2", "A02", 300, 1380, 10) + mk("2", "A01A", 300, 1200, 10) +
            mk("3", "A02", 360, 1320, 6))  # Sunday: only A02, 06:00-22:00
    trips = pd.DataFrame([(t, r, s) for t, r, s, _, _ in rows], columns=["trip_id", "route_id", "service_id"])
    spans = pd.DataFrame({"first": [a for *_, a, _ in rows], "last": [b for *_, b in rows]},
                         index=[t for t, *_ in rows])
    routes = pd.DataFrame({"route_id": ["A11", "A02", "A01A"], "route_short_name": ["A11", "A02", "A01A"]})
    return trips, spans, routes

def test_window_parsing():
    assert D.parse_window("05:00-09:00 / 16:00-20:00") == (300, 1200)
    assert D.parse_window("NO OPERA") is None

def test_mapping_proposed():
    trips, spans, routes = build()
    detail, scores, unmatched = D.score_service_ids(trips, spans, routes, PDF)
    m = D.propose_mapping(scores)
    assert m["1"]["daytype"] == "HABIL"
    assert m["2"]["daytype"] == "SABADO"
    assert m["3"]["daytype"] == "DOM_FEST"

def test_prefix_match_unions_variants():
    t = {"A01A": {"HABIL": (300, 1200), "SABADO": None, "DOM_FEST": None},
         "A01B": {"HABIL": (360, 1260), "SABADO": None, "DOM_FEST": None}}
    assert D.pdf_windows_for("A01", t)["HABIL"] == (300, 1260)


if __name__ == "__main__":
    test_window_parsing()
    test_mapping_proposed()
    test_prefix_match_unions_variants()
    print("All test_diagnose_service_daytypes.py tests passed.")
