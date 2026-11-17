import json
import os
import schedule_reference as SR


def test_parse_window_variants():
    assert SR.parse_window("5:00-20:00") == (300, 1200)
    assert SR.parse_window("5:00 - 14:00") == (300, 840)
    assert SR.parse_window("5:00 -19:00") == (300, 1140)
    assert SR.parse_window("6:00-7:00 / 14:00-22:00") == (360, 1320)
    assert SR.parse_window("NO OPERA") is None and SR.parse_window("") is None and SR.parse_window(None) is None


def test_union_over_variants():
    rows = [{"route_code": "A02", "habil": "5:00-21:00", "sabado": "5:00-21:00", "dom_fest": "6:00-21:00"},
            {"route_code": "A02", "habil": "21:00-23:00", "sabado": "21:00-23:00", "dom_fest": "21:00-22:00"},
            {"route_code": "A13A", "habil": "NO OPERA", "sabado": "NO OPERA", "dom_fest": "7:00-14:00"}]
    t = SR.table_from_listado(rows)
    assert t["A02"]["HABIL"] == (300, 1380) and t["A02"]["DOM_FEST"] == (360, 1320)
    assert t["A13A"]["HABIL"] is None and t["A13A"]["DOM_FEST"] == (420, 840)


class Down:
    def get(self, *a, **k):
        raise ConnectionError("blocked")


def test_snapshot_fallback_and_pdf_only_codes():
    logs = []
    table, src = SR.load_reference(session=Down(), log=logs.append)
    assert SR.newest_snapshot() and os.path.basename(SR.newest_snapshot()).startswith("listado_rutas_snapshot_")
    assert src["A01A"] == "snapshot" and table["A01A"]["DOM_FEST"] is None
    assert src["A70"] == "snapshot" and table["A70"]["SABADO"] == (300, 1260)   # 5:00-21:00
    assert src.get("A01B") == "pdf" and src.get("A73") == "pdf"                  # only on the poster
    assert any("snapshot" in m for m in logs)


def test_live_response_wins_over_snapshot():
    class Live:
        def get(self, *a, **k):
            class R:
                def raise_for_status(self): pass
                def json(self): return [{"route_code": "A01A", "habil": "6:00-7:00", "sabado": "NO OPERA", "dom_fest": "NO OPERA"}]
            return R()
    table, src = SR.load_reference(session=Live(), log=lambda m: None)
    assert src["A01A"] == "live" and table["A01A"]["HABIL"] == (360, 420)
    assert src["A02"] == "pdf"          # not in the (tiny) live answer -> poster fallback


if __name__ == "__main__":
    test_parse_window_variants(); test_union_over_variants()
    test_snapshot_fallback_and_pdf_only_codes(); test_live_response_wins_over_snapshot()
    print("All test_schedule_reference.py tests passed.")
