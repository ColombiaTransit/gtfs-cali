"""
Names and short names for routes.txt from the decoded route code
================================================================
route_short_name = decoded code (A01A, P21E ...); route_long_name = the variant
description of Metro Cali's listadoRutas.php, e.g. "Hábil Normal - ESTACIÓN SAN BOSCO
- CAM - CENTRO" -> "ESTACIÓN SAN BOSCO - CAM - CENTRO" (title-cased later by fix.py).
"""
import re

import pandas as pd

import route_codes as RC

# Preference when a code has several variants: the everyday route first.
VARIANT_PRIORITY = ["hábil normal", "domingo normal", "sábado domingo normal", "hábil desvío",
                    "sábado domingo larga", "sábado domingo corta", "domingo desvío", "domingo ciclovía"]
DASH = r"[-\u2013\u2014]"
LABEL = re.compile(r"^\s*((?:h[aá]bil|s[aá]bado|domingo)[^-\u2013\u2014]*?)\s+" + DASH + r"\s+(.*)$", re.I)
STARTS_WITH_LABEL = re.compile(r"^\s*(?:h[aá]bil|s[aá]bados?|domingos?)\b", re.I)


def split_variante(text):
    """'Hábil Normal - ESTACIÓN SAN BOSCO - CAM' -> ('hábil normal', 'ESTACIÓN SAN BOSCO - CAM')."""
    text = str(text or "")
    m = LABEL.match(text)
    if not m:
        return "", text.strip()
    label, name = m.group(1).strip().lower(), m.group(2).strip()
    while True:                      # a doubled label ("Domingo Ciclovía - Hábil Normal - X")
        m = LABEL.match(name)
        if not m:
            return label, name
        name = m.group(2).strip()


def long_names_from_listado(rows):
    """{route_code: (long_name, variant_label_used)} choosing the everyday variant."""
    best = {}
    for r in rows:
        code = str(r.get("route_code", "")).strip().upper()
        label, name = split_variante(r.get("variante"))
        if not code or not name or STARTS_WITH_LABEL.match(name):
            continue                 # no name, or a variant label we could not strip: never publish it
        rank = VARIANT_PRIORITY.index(label) if label in VARIANT_PRIORITY else len(VARIANT_PRIORITY)
        if code not in best or rank < best[code][0]:
            best[code] = (rank, name, label)
    return {c: (n, l) for c, (_, n, l) in best.items()}


def apply_route_codes(routes_df, listado_rows):
    """Returns (routes_df, audit_df). Never touches ids it cannot decode."""
    names = long_names_from_listado(listado_rows or [])
    df = routes_df.copy()
    if "route_type" not in df.columns:
        df["route_type"] = "3"
    df["route_type"] = df["route_type"].astype("object")
    for col in ("route_short_name", "route_long_name"):
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = df[col].astype("object")
    audit = []
    for i, row in df.iterrows():
        rid = str(row["route_id"]).strip()
        code, source, long_name, variant = None, "", None, ""
        if rid.upper() == RC.CABLE_ID:
            code, source, long_name = RC.CABLE_ID, "cable", RC.CABLE_LONG_NAME
            df.at[i, "route_type"] = "6"   # GTFS: 6 = gondola / suspended cable car
        else:
            code = RC.decode_route_id(rid)
            source = "decoded" if code else "undecodable"
            if code in names:
                long_name, variant = names[code]
        if code:
            df.at[i, "route_short_name"] = code
        if long_name:
            df.at[i, "route_long_name"] = long_name
        audit.append({"route_id": rid, "route_short_name": code or "", "route_long_name": long_name or "",
                      "code_source": source, "in_listado": bool(code in names), "variant_used": variant})
    return df, pd.DataFrame(audit)
