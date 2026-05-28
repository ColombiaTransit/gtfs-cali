import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://services9.arcgis.com/8rJ42n9yWry0I4K4/arcgis/rest/services/GTFS/FeatureServer"

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

session = requests.Session()


def get_json(url, params=None):
    response = session.get(url, params=params)
    response.raise_for_status()
    return response.json()


def discover_layers():
    metadata = get_json(
        BASE_URL,
        params={"f": "json"}
    )

    layers = metadata.get("layers", [])
    if not layers:
        raise RuntimeError("No layers found")

    return layers


def fetch_all_features(layer_id):
    layer_url = f"{BASE_URL}/{layer_id}"

    meta = get_json(layer_url, {"f": "json"})
    max_record_count = meta.get("maxRecordCount", 2000)

    offset = 0
    all_features = []

    while True:
        data = get_json(
            f"{layer_url}/query",
            {
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "false",
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": max_record_count
            }
        )

        features = data.get("features", [])
        if not features:
            break

        for feature in features:
            all_features.append(feature.get("attributes", {}))

        print(f"Layer {layer_id}: downloaded {len(all_features)}")

        if len(features) < max_record_count:
            break

        offset += max_record_count

    return pd.DataFrame(all_features)


def normalize_layer_name(name):
    """
    Convert ArcGIS layer names to GTFS filenames.

    Examples:
    Stop Times -> stop_times
    Agency -> agency
    Calendar Dates -> calendar_dates
    """
    name = name.lower().strip()
    name = name.replace(" ", "_")

    aliases = {
        "stops": "stops",
        "routes": "routes",
        "trips": "trips",
        "stop_times": "stop_times",
        "calendar": "calendar",
        "calendar_dates": "calendar_dates",
        "agency": "agency",
        "shapes": "shapes",
        "feed_info": "feed_info",
        "transfers": "transfers",
        "fare_attributes": "fare_attributes",
        "fare_rules": "fare_rules",
    }

    for key, value in aliases.items():
        if key in name:
            return value

    return name


def convert_dates(df):
    """
    Convert ArcGIS timestamps to GTFS YYYYMMDD where possible.
    """
    for col in df.columns:
        if "date" in col.lower():
            try:
                s = pd.to_datetime(df[col], errors="coerce")
                if s.notna().any():
                    df[col] = s.dt.strftime("%Y%m%d")
            except Exception:
                pass

    return df


def save_gtfs(layers):
    txt_files = []

    for layer in layers:
        layer_id = layer["id"]
        layer_name = layer["name"]

        print(f"Processing {layer_name}")

        df = fetch_all_features(layer_id)

        if df.empty:
            print(f"Skipping empty layer {layer_name}")
            continue

        df.columns = [c.lower() for c in df.columns]
        df = convert_dates(df)

        gtfs_name = normalize_layer_name(layer_name)
        path = OUTPUT_DIR / f"{gtfs_name}.txt"

        df.to_csv(path, index=False)
        txt_files.append(path)

    zip_path = OUTPUT_DIR / "gtfs.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in txt_files:
            zf.write(file, arcname=file.name)

    print(f"Created {zip_path}")


def main():
    layers = discover_layers()

    print("Layers found:")
    for layer in layers:
        print(f"- {layer['id']}: {layer['name']}")

    save_gtfs(layers)


if __name__ == "__main__":
    main()
