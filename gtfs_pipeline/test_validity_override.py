import pandas as pd
from datetime import date
import validity_override as VO

def test_synthesizes_and_drops_stale():
    import os
    os.environ["VALIDITY_ALLOW_UNMAPPED"] = "1"      # ids "1"/"2" have no confirmed day type
    cd = pd.DataFrame({"service_id": ["1", "1"], "date": ["20250101", "20261201"], "exception_type": ["1", "2"]})
    cal, cd2, s, e = VO.apply_validity_override(None, cd, {"1", "2"}, today=date(2026, 11, 17), days=183, log=lambda m: None)
    assert (s, e) == ("20261117", "20270519")
    assert set(cal["service_id"]) == {"1", "2"} and (cal["start_date"] == s).all()
    assert list(cd2["date"]) == ["20261201"]
    del os.environ["VALIDITY_ALLOW_UNMAPPED"]


def test_unmapped_service_ids_fail_hard():
    import os
    os.environ.pop("VALIDITY_ALLOW_UNMAPPED", None)
    try:
        VO.apply_validity_override(None, None, {"8099041", "999"}, today=date(2026, 11, 17), days=183, log=lambda m: None)
    except ValueError as e:
        assert "999" in str(e) and "SERVICE_DAYTYPE" in str(e)
    else:
        raise AssertionError("an unmapped service_id must stop the build")
    cal, _, _, _ = VO.apply_validity_override(None, None, {"8099041"}, today=date(2026, 11, 17), days=183, log=lambda m: None)
    assert list(cal["service_id"]) == ["8099041"]     # known ids are fine

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
    assert "8099148" not in flags.index                       # festivo service: calendar_dates only
    got = {(r.service_id, r.date, r.exception_type) for r in cd.itertuples()}
    assert ("8099041", "20261208", "2") in got and ("8099148", "20261208", "1") in got  # Tue 8 Dec
    assert ("8099041", "20261225", "2") in got                                       # Fri 25 Dec
    assert ("8099042", "20270102", "2") in got and ("8099148", "20270102", "1") in got  # Sat 2 Jan
    assert not [r for r in got if r[1] == "20270103"]                                  # Sunday festivo
    assert not [r for r in got if r[0] == "8099043"]
    # without festivo dates the festivo service keeps an all-zero calendar row (trips stay valid)
    cal2, cd2, _, _ = VO.apply_validity_override(None, None, {"8099148"}, today=date(2026, 11, 17),
                                                 days=183, log=lambda m: None, festivos=None)
    assert list(cal2["service_id"]) == ["8099148"] and cal2.iloc[0][VO.DAYS].astype(int).sum() == 0


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
    test_unmapped_service_ids_fail_hard()
    test_mapping_and_festivo_exceptions()
    test_festivos_parsing_and_fallback()
    print("All test_validity_override.py tests passed.")
