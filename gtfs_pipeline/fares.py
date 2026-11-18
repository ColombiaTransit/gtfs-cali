"""
Fares (fare_attributes.txt)
===========================
Metro Cali's ArcGIS data carries no fare information, so the fare is a reviewed constant here.

Confirmed: the general MIO fare is COP 3,500 per trip since 2026-01-10 (district decree; Metro Cali bulletin
"Es oficial: pasaje del MIO sera de $3.500 en 2026"). Update FARE_PRICE (and FARE_VALID_FROM) when a new
decree changes it; the pipeline never guesses a value.

Not from an official source (one secondary source: colombiamove.com "Guia de buses MIO en Cali", 2026):
  * one fare for every route, MIO Cable included -> no fare_rules.txt (a fare without rules applies to all routes);
  * transfers within 90 minutes of the first card validation -> transfers empty (unlimited), transfer_duration 5400;
  * no cash on board (card only) -> payment_method 1 (paid before boarding).
Verify against Metro Cali / the decree when possible and change here if it differs.
"""
import pandas as pd

FARE_ID = "general"
FARE_PRICE = 3500
FARE_CURRENCY = "COP"
FARE_VALID_FROM = "2026-01-10"
PAYMENT_METHOD = "1"
TRANSFER_DURATION_S = "5400"
AGENCY_ID = "MIO"


def default_fare_attributes(agency_ids=None):
    """One-row fare_attributes DataFrame; agency_id is only set when the feed has that agency."""
    agency = AGENCY_ID if agency_ids is None or AGENCY_ID in set(agency_ids) else ""
    return pd.DataFrame([{
        "fare_id": FARE_ID, "price": str(FARE_PRICE), "currency_type": FARE_CURRENCY,
        "payment_method": PAYMENT_METHOD, "transfers": "", "agency_id": agency, "transfer_duration": TRANSFER_DURATION_S,
    }])
