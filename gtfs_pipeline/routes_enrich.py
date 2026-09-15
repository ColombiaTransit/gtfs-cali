"""
routes_enrich.py
=================
Cross-references our reconstructed routes.txt (which has route_id and
route_type but NO name fields at all - see reconstruct.py) against Metro
Cali's separate "rutas" open-data layer, which carries real route names
and rich schedule/service metadata.

No network calls in here - see enrich_routes.py for the CLI/network wrapper.

--- Source schema (confirmed live) -----------------------------------------
RUTA        - route/variant code, e.g. "A01A", "A01B", "A02", "A11B".
              Base route code + an OPTIONAL trailing letter for direction/
              variant (A01A/A01B are the two directions of route A01; A02
              has only one variant so carries no suffix).
NOMBRE      - human-readable description, e.g.
              "ESTACIÓN SAN BOSCO - CAM - CENTRO". Populated on every row.
              This is what becomes route_long_name.
SERVICIO    - e.g. "ALIMENTADOR" (feeder), "TRONCAL", etc.
TIPOLOGIA   - vehicle type, e.g. "PADRON", "COMPLEMENTARIO".
HABIL / SABADO / DOM_FEST - operating-hours windows per day type.
(plus LineString/MultiLineString geometry - real route shapes, not used
 for name matching here, but worth cross-checking against our
 reconstructed shapes.txt separately if their quality is ever in doubt.)

--- Matching strategy -------------------------------------------------------
1. Derive a base route code from RUTA by stripping a single trailing
   uppercase letter IF doing so still leaves the code ending in a digit
   (handles "A01A" -> "A01", "A11B" -> "A11", while leaving "A02" alone
   since it has no suffix to strip).
2. Match base codes against route_id, tracking coverage the same way
   stops_enrich.py does (only trust the match if it clears a reasonable
   coverage bar).
3. Multiple RUTA rows can share a base route_id (one per direction/variant).
   For route_long_name, prefer the row whose RUTA equals the base code
   exactly (the "primary" variant with no suffix); otherwise take the
   first variant found, and log when different variants disagree on NOMBRE
   so a human can sanity-check which one was picked.
"""
import re
from collections import defaultdict

import pandas as pd

DIAGNOSTICS = []


def diag(msg):
    DIAGNOSTICS.append(msg)
    print(f"  [diagnostic] {msg}")


def base_route_code(ruta):
    """'A01A' -> 'A01', 'A11B' -> 'A11', 'A02' -> 'A02' (no suffix to strip)."""
    if pd.isna(ruta):
        return None
    s = str(ruta).strip()
    m = re.match(r"^(.*\d)([A-Z])$", s)
    return m.group(1) if m else s


def build_route_variant_map(ext_df):
    """Group ext_df rows by base route code. Returns
    {base_code: [(RUTA, row_index), ...]}, and logs how many distinct base
    codes were derived from how many source rows."""
    groups = defaultdict(list)
    for idx, row in ext_df.iterrows():
        base = base_route_code(row.get("RUTA"))
        if base:
            groups[base].append((str(row.get("RUTA")).strip(), idx))
    diag(f"rutas: {len(ext_df)} source row(s) grouped into {len(groups)} "
         f"distinct base route code(s)")
    return groups


def pick_best_variant(base_code, variants, ext_df, name_col):
    """Among rows sharing a base route code, prefer the one whose RUTA
    equals the base code exactly (no direction suffix); else take the
    first. Logs a note if variants disagree on the name text."""
    exact = [idx for ruta, idx in variants if ruta == base_code]
    chosen_idx = exact[0] if exact else variants[0][1]

    names = {str(ext_df.loc[idx, name_col]).strip() for _, idx in variants
             if pd.notna(ext_df.loc[idx, name_col])}
    if len(names) > 1:
        diag(f"route '{base_code}': {len(variants)} variant(s) disagree on "
             f"name ({names}) - using the one from RUTA='{ext_df.loc[chosen_idx, 'RUTA']}'")
    return chosen_idx


def best_id_match(our_routes_df, route_groups):
    """Compare our route_id values against the derived base route codes.
    Returns (coverage_fraction, {route_id: chosen_ext_row_index})."""
    our_ids = list(our_routes_df["route_id"].astype(str))
    matches = {}
    for rid in our_ids:
        if rid in route_groups:
            matches[rid] = route_groups[rid]
    coverage = len(matches) / len(our_ids) if our_ids else 0
    diag(f"route_id coverage via RUTA base-code match: {len(matches)}/{len(our_ids)} "
         f"({coverage:.0%})")
    return coverage, matches


def enrich_routes(our_routes_df, ext_df, name_col="NOMBRE"):
    """Returns (enriched_df, stats_dict). enriched_df is our_routes_df with
    route_long_name filled in wherever a match was found (unchanged
    elsewhere - fix.py's placeholder logic still handles those)."""
    if name_col not in ext_df.columns:
        diag(f"WARNING: expected name column '{name_col}' not found in "
             f"rutas data (columns: {list(ext_df.columns)}). Cannot enrich "
             f"route_long_name.")
        return our_routes_df.copy(), {"matched": 0, "total": len(our_routes_df)}

    route_groups = build_route_variant_map(ext_df)
    coverage, matches = best_id_match(our_routes_df, route_groups)

    if coverage < 0.5:
        diag(f"WARNING: coverage too low ({coverage:.0%}) to trust - route "
             f"naming convention may differ from expected pattern. Check "
             f"the RUTA sample values and adjust base_route_code() if needed.")
        return our_routes_df.copy(), {"matched": 0, "total": len(our_routes_df)}

    enriched = our_routes_df.copy()
    if "route_long_name" not in enriched.columns:
        enriched["route_long_name"] = pd.NA

    matched_count = 0
    for route_id, variants in matches.items():
        chosen_idx = pick_best_variant(route_id, variants, ext_df, name_col)
        name = ext_df.loc[chosen_idx, name_col]
        if pd.notna(name) and str(name).strip():
            enriched.loc[enriched["route_id"].astype(str) == route_id, "route_long_name"] = str(name).strip()
            matched_count += 1

    total = len(our_routes_df)
    diag(f"TOTAL: {matched_count}/{total} route(s) enriched with a real "
         f"long name ({matched_count/total:.0%})" if total else "TOTAL: 0 routes to enrich")

    return enriched, {"matched": matched_count, "total": total}
