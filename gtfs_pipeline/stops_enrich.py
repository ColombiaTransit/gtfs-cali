"""
stops_enrich.py
===============
Cross-references our reconstructed stops.txt (which has no stop_name at
all - see reconstruct.py) against Metro Cali's separate "ptosparadas"
open-data layer, which appears to carry real stop names/corridor info.

We don't know ptosparadas' exact field names ahead of time, so this module
is diagnostic-first, same philosophy as reconstruct.py: dump the discovered
schema, auto-detect plausible ID/name columns by substring heuristics, and
report match statistics loudly rather than silently trusting a guess.

Matching strategy, in order of preference:
  1. ID match: if a ptosparadas column's values overlap heavily with our
     stop_id values (or a normalized variant - strip non-digits, strip
     leading zeros), use it directly. Most reliable when it works.
  2. Spatial nearest-neighbor: for stops not matched by ID, find the
     nearest ptosparadas point within STOPS_ENRICH_MAX_DISTANCE_M and use
     its name field. Works regardless of ID-scheme differences, since both
     datasets describe the same physical stops.
  3. Unmatched stops are left alone (fix.py's placeholder fallback still
     applies to them).

No network calls in here - see enrich_stops.py for the CLI/network wrapper.
"""
import math
import re
import unicodedata
from collections import Counter

import pandas as pd

from gtfs_common import STOPS_ENRICH_MAX_DISTANCE_M

DIAGNOSTICS = []


def diag(msg):
    DIAGNOSTICS.append(msg)
    print(f"  [diagnostic] {msg}")


def normalize_colname(name):
    nfkd = unicodedata.normalize("NFKD", str(name))
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", ascii_str.lower())


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------
# Schema discovery
# --------------------------------------------------------------------------

NAME_HINTS_PRIMARY = ["direccion", "nombre", "name", "paradero", "estacion", "station", "denominac"]
NAME_HINTS_SECONDARY = ["complement", "descripcion", "desc", "sitio", "ubicacion"]
ID_HINTS = ["codigo", "cod", "id"]
ID_EXCLUDE = ["objectid", "globalid", "fid"]  # ArcGIS/row-sequence bookkeeping fields, never a real join key


def _is_blank(v):
    return pd.isna(v) or not str(v).strip()


def discover_schema(ext_df):
    """Print every column with a couple of sample values, and return
    (primary_name_cols, secondary_name_cols, id_cols) ranked by heuristic
    relevance. Two name tiers because real Metro Cali data has address-like
    fields (DIRECCION) that are populated on every row, and separate
    "description" fields (DESCRIP) that are usually blank - a single global
    name column choice would silently pick the wrong (blank) one."""
    diag(f"ptosparadas columns discovered: {list(ext_df.columns)}")
    for col in ext_df.columns:
        sample = ext_df[col].dropna().astype(str).head(3).tolist()
        diag(f"  {col}: sample values = {sample}")

    primary, secondary, id_cols = [], [], []
    for col in ext_df.columns:
        norm = normalize_colname(col)
        if any(h in norm for h in ID_EXCLUDE):
            pass  # never a name or id candidate
        elif any(h in norm for h in NAME_HINTS_PRIMARY):
            primary.append(col)
        elif any(h in norm for h in NAME_HINTS_SECONDARY):
            secondary.append(col)
        elif any(h in norm for h in ID_HINTS):
            id_cols.append(col)

    diag(f"candidate name column(s), primary (address-like): {primary}")
    diag(f"candidate name column(s), secondary (descriptive/landmark): {secondary}")
    diag(f"candidate id column(s): {id_cols}")
    return primary, secondary, id_cols


# --------------------------------------------------------------------------
# ID-based matching
# --------------------------------------------------------------------------

def normalize_id(value):
    """Strip non-alphanumerics and leading zeros so '00123' == '123' == 'ID123'
    where sensible, without conflating genuinely different short IDs."""
    if pd.isna(value):
        return None
    s = re.sub(r"[^0-9A-Za-z]", "", str(value)).upper()
    stripped = s.lstrip("0")
    return stripped if stripped else s


def try_id_match(our_stop_ids, ext_df, id_col):
    """Returns (coverage_fraction, {stop_id: ext_row_index}) for one candidate
    ID column."""
    our_norm = {normalize_id(sid): sid for sid in our_stop_ids}
    matches = {}
    for idx, raw in ext_df[id_col].items():
        norm = normalize_id(raw)
        if norm in our_norm:
            matches[our_norm[norm]] = idx
    coverage = len(matches) / len(our_stop_ids) if our_stop_ids else 0
    return coverage, matches


def best_id_match(our_stops_df, ext_df, id_cols):
    """Try every candidate ID column, return the best one if it clears a
    reasonable coverage bar, else (None, 0, {})."""
    our_ids = list(our_stops_df["stop_id"])
    best = (None, 0.0, {})
    for col in id_cols:
        coverage, matches = try_id_match(our_ids, ext_df, col)
        diag(f"ID column '{col}': {len(matches)}/{len(our_ids)} stops match "
             f"({coverage:.0%} coverage)")
        if coverage > best[1]:
            best = (col, coverage, matches)
    return best


# --------------------------------------------------------------------------
# Spatial fallback matching
# --------------------------------------------------------------------------

def spatial_match(our_stops_df, ext_df, max_distance_m=STOPS_ENRICH_MAX_DISTANCE_M,
                   already_matched_stop_ids=None):
    """For each unmatched stop, find the nearest ext_df point (by ext_lat/
    ext_lon) within max_distance_m. O(n*m) - fine for a few thousand stops;
    swap for a proper spatial index if this ever needs to scale past ~20k."""
    already_matched_stop_ids = already_matched_stop_ids or set()
    matches = {}  # stop_id -> (ext_row_index, distance_m)
    distances = []

    ext_points = list(zip(ext_df.index, ext_df["ext_lat"], ext_df["ext_lon"]))

    for _, row in our_stops_df.iterrows():
        if row["stop_id"] in already_matched_stop_ids:
            continue
        if pd.isna(row["stop_lat"]) or pd.isna(row["stop_lon"]):
            continue
        best_idx, best_dist = None, float("inf")
        for ext_idx, ext_lat, ext_lon in ext_points:
            if pd.isna(ext_lat) or pd.isna(ext_lon):
                continue
            d = haversine_m(row["stop_lat"], row["stop_lon"], ext_lat, ext_lon)
            if d < best_dist:
                best_dist, best_idx = d, ext_idx
        if best_idx is not None and best_dist <= max_distance_m:
            matches[row["stop_id"]] = (best_idx, best_dist)
            distances.append(best_dist)

    if distances:
        diag(f"spatial match: {len(matches)} additional stop(s) matched within "
             f"{max_distance_m}m (mean distance {sum(distances)/len(distances):.1f}m, "
             f"max {max(distances):.1f}m)")
    else:
        diag(f"spatial match: 0 additional stops matched within {max_distance_m}m")
    return matches


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def build_row_name(row, primary_cols, secondary_cols):
    """Cascading per-row name pick: first non-blank primary field wins; if
    a non-blank secondary field also exists and adds distinct information,
    append it as extra context (e.g. "Av 15 Oe entre Cl 7 y 8 (Bajo
    Aguacatal)"). Falls back to the first non-blank secondary field alone
    if no primary field had a value. Returns None if nothing usable."""
    base = None
    for col in primary_cols:
        v = row.get(col)
        if not _is_blank(v):
            base = str(v).strip()
            break

    extra = None
    for col in secondary_cols:
        v = row.get(col)
        if not _is_blank(v):
            candidate = str(v).strip()
            if candidate != base:
                extra = candidate
            break

    if base and extra:
        return f"{base} ({extra})"
    if base:
        return base
    if extra:
        return extra
    return None


def enrich_stops(our_stops_df, ext_df):
    """Returns (enriched_df, stats_dict). enriched_df is our_stops_df with a
    new 'stop_name' column filled in wherever a match was found (NaN
    elsewhere - fix.py's placeholder logic still handles those)."""
    name_primary, name_secondary, id_cols = discover_schema(ext_df)

    if not name_primary and not name_secondary:
        diag("WARNING: no plausible name column found in ptosparadas - "
             "cannot enrich stop_name. Check the column dump above and "
             "adjust NAME_HINTS_PRIMARY/NAME_HINTS_SECONDARY in "
             "stops_enrich.py if a real name column was missed.")
        return our_stops_df.copy(), {"id_matched": 0, "spatial_matched": 0, "total": len(our_stops_df)}

    id_col, id_coverage, id_matches = (None, 0.0, {})
    if id_cols:
        id_col, id_coverage, id_matches = best_id_match(our_stops_df, ext_df, id_cols)

    use_id = id_coverage >= 0.5
    if use_id:
        diag(f"using ID column '{id_col}' as primary match ({id_coverage:.0%} coverage)")
    else:
        diag(f"ID coverage too low ({id_coverage:.0%}) to trust - using spatial "
             f"matching for all stops instead")
        id_matches = {}

    spatial_matches = spatial_match(
        our_stops_df, ext_df, already_matched_stop_ids=set(id_matches.keys())
    )

    enriched = our_stops_df.copy()
    enriched["stop_name"] = pd.NA
    n_blank_skipped = 0
    for stop_id, ext_idx in id_matches.items():
        name = build_row_name(ext_df.loc[ext_idx], name_primary, name_secondary)
        if name:
            enriched.loc[enriched["stop_id"] == stop_id, "stop_name"] = name
        else:
            n_blank_skipped += 1
    for stop_id, (ext_idx, _dist) in spatial_matches.items():
        name = build_row_name(ext_df.loc[ext_idx], name_primary, name_secondary)
        if name:
            enriched.loc[enriched["stop_id"] == stop_id, "stop_name"] = name
        else:
            n_blank_skipped += 1

    total = len(our_stops_df)
    matched = enriched["stop_name"].notna().sum()
    diag(f"TOTAL: {matched}/{total} stops enriched with a real name "
         f"({matched/total:.0%})" if total else "TOTAL: 0 stops to enrich")
    if n_blank_skipped:
        diag(f"NOTE: {n_blank_skipped} row(s) matched a ptosparadas record "
             f"but every name-like field on it was blank - left unmatched")

    return enriched, {
        "id_matched": len(id_matches), "spatial_matched": len(spatial_matches),
        "total": total, "id_column_used": id_col if use_id else None,
    }
