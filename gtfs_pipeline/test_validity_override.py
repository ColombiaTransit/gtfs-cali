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


if __name__ == "__main__":
    test_synthesizes_and_drops_stale()
    test_keeps_weekdays_and_empty_dates_none()
    print("All test_validity_override.py tests passed.")
