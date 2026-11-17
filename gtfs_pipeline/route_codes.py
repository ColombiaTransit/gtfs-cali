"""
Route codes decoded from the internal route_id
==============================================
The ArcGIS `Lines.GRouteID` is not an opaque number: it encodes the real MIO
route code (verified against all 91 codes of Metro Cali's listadoRutas.php).

    first digit : route family   1 = T (troncal), 2 = P (pretroncal),
                                 3 = A (alimentadora), 4 = E (expresa)
    next 2      : route number
    4th digit   : (optional) letter, 1 = A, 2 = B, 3 = C ...

    112 -> T12     302 -> A02     2211 -> P21A     2215 -> P21E
    3122 -> A12B   421 -> E21     3012 -> A01B     3351 -> A35A
"""
import re

FAMILY = {"1": "T", "2": "P", "3": "A", "4": "E"}
FAMILY_DIGIT = {v: k for k, v in FAMILY.items()}
CABLE_ID = "MIOCABLE"
CABLE_LONG_NAME = "MIO Cable: Cañaveralejo - Tierra Blanca - Lleras Camargo - Brisas de Mayo"


def decode_route_id(route_id):
    """'2211' -> 'P21A'; None when the id does not follow the pattern."""
    s = str(route_id).strip()
    if not s.isdigit() or s[0] not in FAMILY:
        return None
    if len(s) == 3:
        return FAMILY[s[0]] + s[1:]
    if len(s) == 4 and 1 <= int(s[3]) <= 26:
        return FAMILY[s[0]] + s[1:3] + chr(64 + int(s[3]))
    return None


def encode_route_code(code):
    """'P21A' -> '2211' (inverse of decode_route_id), e.g. to map a bus's line code to a route_id."""
    m = re.fullmatch(r"([TPAE])(\d{2})([A-Z]?)", str(code).strip().upper())
    if not m:
        return None
    fam, num, letter = m.groups()
    return FAMILY_DIGIT[fam] + num + (str(ord(letter) - 64) if letter else "")
