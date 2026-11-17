import json
import glob
import os
import pandas as pd
import route_codes as RC
import routes_from_codes as RFC

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def test_decode_and_encode_roundtrip():
    cases = {"112": "T12", "302": "A02", "2211": "P21A", "2215": "P21E", "3122": "A12B",
             "421": "E21", "3012": "A01B", "3351": "A35A", "275": "P75", "373": "A73"}
    for rid, code in cases.items():
        assert RC.decode_route_id(rid) == code, rid
        assert RC.encode_route_code(code) == rid, code


def test_undecodable_ids():
    for bad in ["MIOCABLE", "", "5011", "3010", "31", "A01", "3 11"]:
        assert RC.decode_route_id(bad) is None, bad
    assert RC.encode_route_code("X01") is None


def test_variant_split_and_priority():
    assert RFC.split_variante("Hábil Normal - ESTACIÓN SAN BOSCO - CAM") == ("hábil normal", "ESTACIÓN SAN BOSCO - CAM")
    assert RFC.split_variante("Sábado Domingo Corta - UNIVERSIDADES - SAN BUENAVENTURA")[0] == "sábado domingo corta"
    rows = [{"route_code": "A13A", "variante": "Domingo Ciclovía - UNIVERSIDADES - EL INGENIO"},
            {"route_code": "A13A", "variante": "Hábil Normal - UNIVERSIDADES - LILI - EL INGENIO"}]
    assert RFC.long_names_from_listado(rows)["A13A"] == ("UNIVERSIDADES - LILI - EL INGENIO", "hábil normal")


def test_snapshot_covers_every_decodable_route():
    rows = json.load(open(sorted(glob.glob(os.path.join(DATA, "listado_rutas_snapshot_*.json")))[-1], encoding="utf-8"))
    codes = {r["route_code"] for r in rows}
    produced = {RC.decode_route_id(RC.encode_route_code(c)) for c in codes}
    assert produced == codes                                   # bijective for all 91 live codes
    routes = pd.DataFrame({"route_id": [RC.encode_route_code(c) for c in sorted(codes)] + ["3012", "373", "MIOCABLE"],
                           "route_short_name": "", "route_long_name": ""})
    out, audit = RFC.apply_route_codes(routes, rows)
    assert (audit.code_source != "undecodable").all() and len(out) == 94
    assert out.loc[out.route_id == "2211", "route_short_name"].iloc[0] == "P21A"
    assert out.loc[out.route_id == "3011", "route_long_name"].iloc[0].startswith("ESTACIÓN SAN BOSCO")
    assert not audit.loc[~audit.route_id.isin(["3012", "373", "MIOCABLE"]), "route_long_name"].eq("").any()
    assert out.loc[out.route_id == "3012", "route_short_name"].iloc[0] == "A01B"      # not in listado: code only
    assert audit.set_index("route_id").loc["3012", "in_listado"] == False
    assert out.loc[out.route_id == "MIOCABLE", "route_long_name"].iloc[0].startswith("MIO Cable")


if __name__ == "__main__":
    test_decode_and_encode_roundtrip(); test_undecodable_ids()
    test_variant_split_and_priority(); test_snapshot_covers_every_decodable_route()
    print("All test_route_codes.py tests passed.")
