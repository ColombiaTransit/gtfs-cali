import pandas as pd
from datetime import date
import validity_override as VO

def test_synthesizes_and_drops_stale():
    cd = pd.DataFrame({"service_id": ["1", "1"], "date": ["20250101", "20261201"], "exception_type": ["1", "2"]})
    cal, cd2, s, e = VO.apply_validity_override(None, cd, {"1", "2"}, today=date(2026, 11, 17), days=183, log=lambda m: None)
    assert (s, e) == ("20261117", "20270519")
    assert set(cal["service_id"]) == {"1", "2"} and (cal["start_date"] == s).all()
    assert list(cd2["date"]) == ["20261201"]

def test_keeps_weekdays_and_empty_dates_none():
    cal0 = pd.DataFrame([{"service_id": "A", "monday": "1", "tuesday": "0", "wednesday": "0", "thursday": "0",
                          "friday": "0", "saturday": "0", "sunday": "0", "start_date": "20240101", "end_date": "20240201"}])
    cd = pd.DataFrame({"service_id": ["A"], "date": ["20250101"], "exception_type": ["1"]})
    cal, cd2, s, e = VO.apply_validity_override(cal0, cd, {"A"}, today=date(2026, 11, 17), days=183, log=lambda m: None)
    assert cal.iloc[0]["monday"] == "1" and cal.iloc[0]["end_date"] == e and cd2 is None



def test_mapping_and_festivo_exceptions():
    from datetime import date
    import validity_override as VO
    cal, cd, s, e = VO.apply_validity_override(
        None, None, {"8099041", "8099042", "8099043", "8099148"}, today=date(2026, 11, 17),
        days=183, log=lambda m: None,
        festivos={date(2026, 12, 8), date(2026, 12, 25), date(2027, 1, 2), date(2027, 1, 3)})
    flags = cal.set_index("service_id")
    assert flags.loc["8099041", ["monday", "friday", "saturday"]].tolist() == ["1", "1", "0"]
    assert flags.loc["8099042", "saturday"] == "1" and flags.loc["8099043", "sunday"] == "1"
    assert flags.loc["8099148", ["monday", "saturday", "sunday"]].tolist() == ["0", "0", "0"]
    got = {(r.service_id, r.date, r.exception_type) for r in cd.itertuples()}
    assert ("8099041", "20261208", "2") in got and ("8099148", "20261208", "1") in got  # Tue 8 Dec
    assert ("8099041", "20261225", "2") in got                                       # Fri 25 Dec
    assert ("8099042", "20270102", "2") in got and ("8099148", "20270102", "1") in got  # Sat 2 Jan
    assert not [r for r in got if r[1] == "20270103"]                                  # Sunday festivo
    assert not [r for r in got if r[0] == "8099043"]


def test_festivos_parsing_and_fallback():
    from datetime import date
    import fetch_festivos as F
    p = {"status": "success", "data": [{"fecha": "2026-12-08"}, {"fecha": "bad"}, {"id": 1}]}
    assert F.parse_festivos_json(p) == {date(2026, 12, 8)}
    assert F.parse_festivos_json({"status": "error"}) == set()

    class Boom:
        def get(self, *a, **k):
            raise ConnectionError("blocked")
    days, src = F.get_festivos(date(2026, 11, 17), date(2027, 1, 31), session=Boom(), log=lambda m: None)
    assert date(2026, 12, 8) in days and "fallback" in src


if __name__ == "__main__":
    test_synthesizes_and_drops_stale()
    test_keeps_weekdays_and_empty_dates_none()
    test_mapping_and_festivo_exceptions()
    test_festivos_parsing_and_fallback()
    print("All test_validity_override.py tests passed.")
