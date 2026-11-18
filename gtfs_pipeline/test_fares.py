import fares as F


def test_default_fare():
    df = F.default_fare_attributes(["MIO"])
    r = df.iloc[0]
    assert len(df) == 1 and r.fare_id == "general" and r.price == "3500" and r.currency_type == "COP"
    assert r.payment_method in ("0", "1") and r.transfers == "" and r.transfer_duration == "5400" and r.agency_id == "MIO"
    assert F.default_fare_attributes(["OTHER"]).iloc[0].agency_id == ""


if __name__ == "__main__":
    test_default_fare()
    print("All test_fares.py tests passed.")
