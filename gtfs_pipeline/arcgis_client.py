"""
arcgis_client.py
=================
Small shared HTTP client for the ArcGIS REST API, used by both download.py
(the main GTFS reconstruction) and enrich_stops.py (cross-referencing the
separate "ptosparadas" stop-names dataset). Kept dependency-free besides
requests/pandas so it's easy to reuse for any future auxiliary Metro Cali
dataset.
"""
import json
import time

import pandas as pd
import requests

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "metrocali-gtfs-pipeline/1.0"})
PAGE_SIZE = 2000
TIMEOUT = 60


def item_url(item_id):
    return f"https://www.arcgis.com/sharing/rest/content/items/{item_id}?f=json"


def get_json(url, **params):
    params.setdefault("f", "json")
    for attempt in range(3):
        try:
            r = SESSION.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(f"ArcGIS API error for {url}: {data['error']}")
            return data
        except (requests.RequestException, json.JSONDecodeError) as e:
            if attempt == 2:
                raise
            print(f"  retry ({attempt+1}/3) after error: {e}")
            time.sleep(2)


def resolve_feature_service_url(item_id, fallback_url=None):
    """Resolve an ArcGIS Hub item id to its underlying FeatureServer URL.
    Falls back to a known/confirmed URL if item resolution fails (e.g. the
    item gets repointed to a new service in a future data release)."""
    try:
        item = get_json(item_url(item_id))
        url = item.get("url")
        if url:
            print(f"Resolved item {item_id} -> {url}")
            return url
        print(f"  item {item_id} has no 'url' field (type={item.get('type')})")
    except Exception as e:
        print(f"  item-API resolution failed for {item_id} ({e})")
    if fallback_url:
        print(f"Using fallback Feature Service URL: {fallback_url}")
        return fallback_url
    raise RuntimeError(f"Could not resolve a FeatureServer URL for item {item_id} "
                        f"and no fallback_url was provided.")


def get_layer_fields(service_url, layer_id):
    """Return the field list (names + types) for a single layer/table, via
    its own metadata endpoint (not the /query endpoint)."""
    info = get_json(f"{service_url}/{layer_id}")
    return [f["name"] for f in info.get("fields", [])]


def list_layers_and_tables(service_url):
    """Return [(id, name, kind)] for every layer and table on a service."""
    info = get_json(service_url)
    layers = info.get("layers", []) or []
    tables = info.get("tables", []) or []
    return [(l["id"], l["name"], "layer") for l in layers] + \
           [(t["id"], t["name"], "table") for t in tables]


def query_all_records(service_url, layer_id, label, retry_on_mismatch=True):
    """Paginate through a layer/table. Returns a DataFrame; point
    geometries add lat/lon columns; polyline geometries add _geom_path."""
    query_url = f"{service_url}/{layer_id}/query"

    server_count = None
    try:
        count_data = get_json(query_url, where="1=1", returnCountOnly="true")
        server_count = count_data.get("count")
        print(f"    [{label}] server reports {server_count} record(s)")
    except Exception as e:
        print(f"    [{label}] could not get server count ({e}), proceeding anyway")

    rows = _paginate(query_url, label)

    if retry_on_mismatch and server_count is not None and len(rows) != server_count:
        print(f"    [{label}] WARNING: server count ({server_count}) != rows fetched "
              f"({len(rows)}). Retrying once with an explicit orderByFields...")
        rows = _paginate(query_url, label, order_by="OBJECTID", tag="retry")
        if len(rows) != server_count:
            print(f"    [{label}] STILL mismatched after retry: server says "
                  f"{server_count}, fetched {len(rows)}. Proceeding with what "
                  f"we have - investigate manually if this table matters: "
                  f"{query_url}?where=1=1&outFields=*&f=json")

    return pd.DataFrame(rows)


def _paginate(query_url, label, order_by=None, tag="fetched"):
    offset = 0
    rows = []
    while True:
        params = {
            "where": "1=1", "outFields": "*", "f": "json",
            "resultOffset": offset, "resultRecordCount": PAGE_SIZE,
            "outSR": 4326, "returnGeometry": "true",
        }
        if order_by:
            params["orderByFields"] = order_by
        try:
            data = get_json(query_url, **params)
        except Exception as e:
            print(f"    [{label}] pagination failed ({e}), returning what we have")
            return rows
        feats = data.get("features", [])
        if not feats:
            break
        for feat in feats:
            attrs = dict(feat.get("attributes", {}))
            geom = feat.get("geometry")
            if geom:
                if "x" in geom and "y" in geom:
                    attrs["_geom_lat"] = geom["y"]
                    attrs["_geom_lon"] = geom["x"]
                elif "paths" in geom and geom["paths"]:
                    path = [pt for part in geom["paths"] for pt in part]
                    attrs["_geom_path"] = [(lat, lon) for lon, lat in path]
            rows.append(attrs)
        got = len(feats)
        offset += got
        print(f"    [{label}] {tag} {offset} records so far...")
        if not data.get("exceededTransferLimit") and got < PAGE_SIZE:
            break
    return rows
