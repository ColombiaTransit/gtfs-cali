"""
Festivos (Colombian public holidays) for the calendar exceptions
================================================================
Primary source: Metro Cali's own list, the one behind the "Busca Rutas MIO" page
(admin/api/festivos.php?action=list), so the exceptions match what MIO actually
operates. Fallback: the `holidays` package (colombia_holidays.py) if the Metro
Cali endpoint is unreachable or returns nothing for the requested window.
"""
from datetime import date, datetime

import requests

FESTIVOS_URL = "https://metrocali.gov.co/newmioapp/mioapp/views/admin/api/festivos.php?action=list"


def parse_festivos_json(payload):
    """{'status':'success','data':[{'fecha':'2026-12-08',...}]} -> set of date."""
    out = set()
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return out
    for row in payload.get("data") or []:
        try:
            out.add(datetime.strptime(str(row["fecha"])[:10], "%Y-%m-%d").date())
        except (KeyError, ValueError):
            continue
    return out


def get_festivos(start, end, session=None, log=print):
    """Returns (set of date within [start, end], source label). start/end are dates."""
    try:
        r = (session or requests).get(FESTIVOS_URL, timeout=30)
        r.raise_for_status()
        days = {d for d in parse_festivos_json(r.json()) if start <= d <= end}
        if days:
            return days, "metrocali festivos.php"
        log("festivos.php returned no dates inside the window - falling back to the holidays package")
    except Exception as e:  # network, JSON, HTTP errors: never fatal
        log(f"festivos.php not usable ({type(e).__name__}: {e}) - falling back to the holidays package")
    from colombia_holidays import colombia_holidays_in_range
    return set(colombia_holidays_in_range(start, end)), "holidays package (fallback)"
